from os import path
import itertools
from collections import defaultdict
from functools import partial
import json
from gusto.diagnostics import Diagnostics, Perturbation, \
    SteadyStateError
from firedrake import FiniteElement, TensorProductElement, HDiv, \
    FunctionSpace, MixedFunctionSpace, VectorFunctionSpace, \
    interval, Function, Mesh, functionspaceimpl,\
    File, dx, op2, par_loop, READ, WRITE, DumbCheckpoint, \
    FILE_CREATE, FILE_READ
import numpy as np


__all__ = ["State", "ShallowWaterState", "CompressibleEulerState"]


class SpaceCreator(object):

    def __call__(self, name, mesh=None, family=None, degree=None):
        try:
            return getattr(self, name)
        except AttributeError:
            value = FunctionSpace(mesh, family, degree)
            setattr(self, name, value)
            return value


class FieldCreator(object):

    def __init__(self, fieldlist=None, xn=None, dumplist=None, pickup=True):
        self.fields = []
        if fieldlist is not None:
            for name, func in zip(fieldlist, xn.split()):
                setattr(self, name, func)
                func.dump = name in dumplist
                func.pickup = pickup
                func.rename(name)
                self.fields.append(func)

    def __call__(self, name, space=None, dump=True, pickup=True):
        try:
            return getattr(self, name)
        except AttributeError:
            value = Function(space, name=name)
            setattr(self, name, value)
            value.dump = dump
            value.pickup = pickup
            self.fields.append(value)
            return value

    def __iter__(self):
        return iter(self.fields)


class State(object):
    """
    Build a model state to keep the variables in, and specify parameters.

    :arg mesh: The :class:`Mesh` to use.
    :arg vertical_degree: integer, required for vertically extruded meshes.
    Specifies the degree for the pressure space in the vertical
    (the degrees for other spaces are inferred). Defaults to None.
    :arg horizontal_degree: integer, the degree for spaces in the horizontal
    (specifies the degree for the pressure space, other spaces are inferred)
    defaults to 1.
    :arg family: string, specifies the velocity space family to use.
    Options:
    "RT": The Raviart-Thomas family (default, recommended for quads)
    "BDM": The BDM family
    "BDFM": The BDFM family
    :arg output: class containing output parameters
    :arg diagnostics: class containing diagnostic methods
    :arg fieldlist: list of prognostic field names
    :arg diagnostic_fields: list of diagnostic field classes
    """

    def __init__(self, mesh, fieldlist,
                 vertical_degree, horizontal_degree,
                 family, *,
                 output=None,
                 diagnostics=None,
                 diagnostic_fields=None):

        self.fieldlist = fieldlist
        if output is None:
            raise RuntimeError("You must provide a directory name for dumping results")
        else:
            self.output = output

        if diagnostics is not None:
            self.diagnostics = diagnostics
        else:
            self.diagnostics = Diagnostics(*fieldlist)
        if diagnostic_fields is not None:
            self.diagnostic_fields = diagnostic_fields
        else:
            self.diagnostic_fields = []

        # The mesh
        self.mesh = mesh

        # Build the spaces
        self._build_spaces(mesh, vertical_degree, horizontal_degree, family)

        # Allocate state
        self._allocate_state()
        if self.output.dumplist is None:
            self.output.dumplist = fieldlist
        self.fields = FieldCreator(fieldlist, self.xn, self.output.dumplist)

        self.dumpfile = None

    def setup_diagnostics(self):
        # add special case diagnostic fields
        for name in self.output.perturbation_fields:
            f = Perturbation(name)
            self.diagnostic_fields.append(f)

        for name in self.output.steady_state_error_fields:
            f = SteadyStateError(self, name)
            self.diagnostic_fields.append(f)

        for diagnostic in self.diagnostic_fields:
            diagnostic.setup(self)
            self.diagnostics.register(diagnostic.name)

    def setup_dump(self, pickup=False):

        # setup dump files
        # check for existence of directory so as not to overwrite
        # output files
        self.dumpdir = path.join("results", self.output.dirname)
        outfile = path.join(self.dumpdir, "field_output.pvd")
        if self.mesh.comm.rank == 0 and "pytest" not in self.output.dirname \
           and path.exists(self.dumpdir) and not pickup:
            raise IOError("results directory '%s' already exists" % self.dumpdir)
        self.dumpcount = itertools.count()
        self.dumpfile = File(outfile, project_output=self.output.project_fields, comm=self.mesh.comm)
        self.diagnostic_data = defaultdict(partial(defaultdict, list))

        # make list of fields to dump
        self.to_dump = [field for field in self.fields if field.dump]

        # if there are fields to be dumped in latlon coordinates,
        # setup the latlon coordinate mesh and make output file
        if len(self.output.dumplist_latlon) > 0:
            mesh_ll = get_latlon_mesh(self.mesh)
            outfile_ll = path.join(self.dumpdir, "field_output_latlon.pvd")
            self.dumpfile_ll = File(outfile_ll,
                                    project_output=self.output.project_fields,
                                    comm=self.mesh.comm)

        # make list of fields to pickup (this doesn't include diagnostic fields)
        self.to_pickup = [field for field in self.fields if field.pickup]

        # make functions on latlon mesh, as specified by dumplist_latlon
        self.to_dump_latlon = []
        for name in self.output.dumplist_latlon:
            f = self.fields(name)
            field = Function(functionspaceimpl.WithGeometry(f.function_space(), mesh_ll), val=f.topological, name=name+'_ll')
            self.to_dump_latlon.append(field)

    def dump(self, t=0, diagnostic_everydump=False, pickup=False):
        """
        Dump output
        :arg t: the current model time (default is zero).
        :arg diagnostic_everydump: dump diagnostics everytime dump()
        is called.
        :arg pickup: recover state from the checkpointing file if true,
        otherwise dump and checkpoint to disk. (default is False).
        """
        if pickup:
            # Open the checkpointing file for writing
            chkfile = path.join(self.dumpdir, "chkpt")
            with DumbCheckpoint(chkfile, mode=FILE_READ) as chk:
                # Recover all the fields from the checkpoint
                for field in self.to_pickup:
                    chk.load(field)
                t = chk.read_attribute("/", "time")
                next(self.dumpcount)

        elif (next(self.dumpcount) % self.output.dumpfreq) == 0:

            print("DBG dumping", t)

            # calculate diagnostic fields
            for field in self.diagnostic_fields:
                field(self)

            # dump fields
            self.dumpfile.write(*self.to_dump)

            # dump fields on latlon mesh
            if len(self.output.dumplist_latlon) > 0:
                self.dumpfile_ll.write(*self.to_dump_latlon)

            # compute diagnostics
            diagnostic_fns = ['min', 'max', 'rms', 'l2']
            for name in self.diagnostics.fields:
                for fn in diagnostic_fns:
                    d = getattr(self.diagnostics, fn)
                    data = d(self.fields(name))
                    self.diagnostic_data[name][fn].append(data)
                if len(self.fields(name).ufl_shape) == 0:
                    data = self.diagnostics.total(self.fields(name))
                    self.diagnostic_data[name]["total"].append(data)

            # Open the checkpointing file (backup version)
            files = ["chkptbk", "chkpt"]
            for file in files:
                chkfile = path.join(self.dumpdir, file)
                with DumbCheckpoint(chkfile, mode=FILE_CREATE) as chk:
                    # Dump all the fields to a checkpoint
                    for field in self.to_pickup:
                        chk.store(field)
                    chk.write_attribute("/", "time", t)

            if diagnostic_everydump:
                self.diagnostic_dump()

        return t

    def diagnostic_dump(self):
        """
        Dump diagnostics dictionary
        """
        with open(path.join(self.dumpdir, "diagnostics.json"), "w") as f:
            f.write(json.dumps(self.diagnostic_data, indent=4))

    def initialise(self, initial_conditions):
        """
        Initialise state variables

        :arg initial_conditions: An iterable of pairs (field_name, pointwise_value)
        """
        for name, ic in initial_conditions:
            f_init = getattr(self.fields, name)
            f_init.assign(ic)
            f_init.rename(name)

    def set_reference_profiles(self, reference_profiles):
        """
        Initialise reference profiles

        :arg reference_profiles: An iterable of pairs (field_name, interpolatory_value)
        """
        for name, profile in reference_profiles:
            field = getattr(self.fields, name)
            ref = self.fields(name+'bar', field.function_space(), False)
            ref.interpolate(profile)

    def _build_spaces(self, mesh, vertical_degree, horizontal_degree, family):
        """
        Build:
        velocity space self.V2,
        pressure space self.V3,
        temperature space self.Vt,
        mixed function space self.W = (V2,V3,Vt)
        """

        self.spaces = SpaceCreator()
        if vertical_degree is not None:
            # horizontal base spaces
            cell = mesh._base_mesh.ufl_cell().cellname()
            S1 = FiniteElement(family, cell, horizontal_degree+1)
            S2 = FiniteElement("DG", cell, horizontal_degree)

            # vertical base spaces
            T0 = FiniteElement("CG", interval, vertical_degree+1)
            T1 = FiniteElement("DG", interval, vertical_degree)

            # build spaces V2, V3, Vt
            V2h_elt = HDiv(TensorProductElement(S1, T1))
            V2t_elt = TensorProductElement(S2, T0)
            V3_elt = TensorProductElement(S2, T1)
            V2v_elt = HDiv(V2t_elt)
            V2_elt = V2h_elt + V2v_elt

            V0 = self.spaces("HDiv", mesh, V2_elt)
            V1 = self.spaces("DG", mesh, V3_elt)
            V2 = self.spaces("HDiv_v", mesh, V2t_elt)

            self.Vv = self.spaces("Vv", mesh, V2v_elt)

            self.W = MixedFunctionSpace((V0, V1, V2))

        else:
            cell = mesh.ufl_cell().cellname()
            V1_elt = FiniteElement(family, cell, horizontal_degree+1)

            V0 = self.spaces("HDiv", mesh, V1_elt)
            V1 = self.spaces("DG", mesh, "DG", horizontal_degree)

            self.W = MixedFunctionSpace((V0, V1))

    def _allocate_state(self):
        """
        Construct Functions to store the state variables.
        """

        W = self.W
        self.xn = Function(W)
        self.xstar = Function(W)
        self.xp = Function(W)
        self.xnp1 = Function(W)
        self.xrhs = Function(W)
        self.xb = Function(W)  # store the old state for diagnostics
        self.dy = Function(W)


def ShallowWaterState(mesh,
                      fieldlist=['u', 'D'],
                      vertical_degree=None,
                      horizontal_degree=1,
                      family="BDM",
                      output=None,
                      diagnostics=None,
                      diagnostic_fields=None):
    return State(mesh, fieldlist,
                 vertical_degree, horizontal_degree, family,
                 output=output,
                 diagnostics=diagnostics,
                 diagnostic_fields=diagnostic_fields)


def CompressibleEulerState(mesh, is_3d=False,
                           fieldlist=['u', 'rho', 'theta'],
                           vertical_degree=1,
                           horizontal_degree=1,
                           family=None,
                           output=None,
                           diagnostics=None,
                           diagnostic_fields=None):
    if family is None:
        if is_3d:
            family = "RT"
        else:
            family = "CG"
    return State(mesh, fieldlist,
                 vertical_degree, horizontal_degree, family,
                 output=output,
                 diagnostics=diagnostics,
                 diagnostic_fields=diagnostic_fields)


def get_latlon_mesh(mesh):
    coords_orig = mesh.coordinates
    mesh_dg_fs = VectorFunctionSpace(mesh, "DG", 1)
    coords_dg = Function(mesh_dg_fs)
    coords_latlon = Function(mesh_dg_fs)
    par_loop("""
for (int i=0; i<3; i++) {
    for (int j=0; j<3; j++) {
        dg[i][j] = cg[i][j];
    }
}
""", dx, {'dg': (coords_dg, WRITE),
          'cg': (coords_orig, READ)})

    # lat-lon 'x' = atan2(y, x)
    coords_latlon.dat.data[:, 0] = np.arctan2(coords_dg.dat.data[:, 1], coords_dg.dat.data[:, 0])
    # lat-lon 'y' = asin(z/sqrt(x^2 + y^2 + z^2))
    coords_latlon.dat.data[:, 1] = np.arcsin(coords_dg.dat.data[:, 2]/np.sqrt(coords_dg.dat.data[:, 0]**2 + coords_dg.dat.data[:, 1]**2 + coords_dg.dat.data[:, 2]**2))
    coords_latlon.dat.data[:, 2] = 0.0

    kernel = op2.Kernel("""
#define PI 3.141592653589793
#define TWO_PI 6.283185307179586
void splat_coords(double **coords) {
    double diff0 = (coords[0][0] - coords[1][0]);
    double diff1 = (coords[0][0] - coords[2][0]);
    double diff2 = (coords[1][0] - coords[2][0]);

    if (fabs(diff0) > PI || fabs(diff1) > PI || fabs(diff2) > PI) {
        const int sign0 = coords[0][0] < 0 ? -1 : 1;
        const int sign1 = coords[1][0] < 0 ? -1 : 1;
        const int sign2 = coords[2][0] < 0 ? -1 : 1;
        if (sign0 < 0) {
            coords[0][0] += TWO_PI;
        }
        if (sign1 < 0) {
            coords[1][0] += TWO_PI;
        }
        if (sign2 < 0) {
            coords[2][0] += TWO_PI;
        }
    }
}
""", "splat_coords")

    op2.par_loop(kernel, coords_latlon.cell_set,
                 coords_latlon.dat(op2.RW, coords_latlon.cell_node_map()))
    return Mesh(coords_latlon)
