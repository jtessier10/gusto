from gusto import *
from firedrake import IcosahedralSphereMesh, Constant, ge, le, exp, cos, \
    conditional, interpolate, SpatialCoordinate, VectorFunctionSpace, \
    Function, assemble, dx, FunctionSpace
from math import pi
import sys
import numpy as np

day = 24.*60.*60.
dt = 240.
if '--running-tests' in sys.argv:
    tmax = dt
else:
    tmax = 6*day

# setup shallow water parameters
R = 6371220.
H = 10000.

# setup input that doesn't change with ref level or dt
fieldlist = ['u', 'D']
parameters = ShallowWaterParameters(H=H)

perturb = True
if perturb:
    dirname = "sw_fluxform_galewsky_jet_perturbed"
else:
    dirname = "sw_fluxform_galewsky_jet_unperturbed"
mesh = IcosahedralSphereMesh(radius=R,
                             refinement_level=6, degree=3)
mesh.init_cell_orientations(SpatialCoordinate(mesh))

timestepping = TimesteppingParameters(dt=dt)
output = OutputParameters(dirname=dirname,
                          dumplist=['D', 'u', 'PotentialVorticity'],
                          dumplist_latlon=['D', 'PotentialVorticity', 'RelativeVorticity'],
                          dumpfreq=10, compute_pv_conservation=True)
diagnostic_fields = [ RelativeVorticity(),
                      ShallowWaterKineticEnergy(),
                      ShallowWaterPotentialEnergy(),
                      ShallowWaterPotentialEnstrophy(),
                      VelocityDivergence()]

state = State(mesh, horizontal_degree=1,
              family="BDM",
              timestepping=timestepping,
              output=output,
              parameters=parameters,
              diagnostics=diagnostics,
              fieldlist=fieldlist,
              diagnostic_fields=diagnostic_fields)

# initial conditions
u0 = state.fields("u")
D0 = state.fields("D")

# get lat lon coordinates
theta, lamda = latlon_coords(mesh)

# expressions for meridional and zonal velocity
u_max = 80.0
theta0 = pi/7.
theta1 = pi/2. - theta0
en = np.exp(-4./((theta1-theta0)**2))
u_zonal_expr = (u_max/en)*exp(1/((theta - theta0)*(theta - theta1)))
u_zonal = conditional(ge(theta, theta0), conditional(le(theta, theta1), u_zonal_expr, 0.), 0.)
u_merid = 0.0

# get cartesian components of velocity
uexpr = sphere_to_cartesian(mesh, u_zonal, u_merid)
u0.project(uexpr, form_compiler_parameters={'quadrature_degree': 12})

Rc = Constant(R)
g = Constant(parameters.g)
Omega = Constant(parameters.Omega)

# Coriolis
x = SpatialCoordinate(mesh)
fexpr = 2*Omega*x[2]/Rc
V = FunctionSpace(mesh, "CG", 3)
f = state.fields("coriolis", V)
f.interpolate(fexpr)  # Coriolis frequency (1/s)

def D_integrand(th):
    # Initial D field is calculated by integrating D_integrand w.r.t. theta
    # Assumes the input is between theta0 and theta1.
    # Note that this function operates on vectorized input.
    from scipy import exp, sin, tan
    f = 2.0*parameters.Omega*sin(th)
    u_zon = (80.0/en)*exp(1.0/((th - theta0)*(th - theta1)))
    return u_zon*(f + tan(th)*u_zon/R)


def Dval(X):
    # Function to return value of D at X
    from scipy import integrate

    # Preallocate output array
    val = np.zeros(len(X))

    angles = np.zeros(len(X))

    # Minimize work by only calculating integrals for points with
    # theta between theta_0 and theta_1.
    # For theta <= theta_0, the integral is 0
    # For theta >= theta_1, the integral is constant.

    # Precalculate this constant:
    poledepth, _ = integrate.fixed_quad(D_integrand, theta0, theta1, n=64)
    poledepth *= -R/parameters.g

    angles[:] = np.arcsin(X[:, 2]/R)

    for ii in range(len(X)):
        if angles[ii] <= theta0:
            val[ii] = 0.0
        elif angles[ii] >= theta1:
            val[ii] = poledepth
        else:
            # Fixed quadrature with 64 points gives absolute errors below 1e-13
            # for a quantity of order 1e-3.
            v, _ = integrate.fixed_quad(D_integrand, theta0, angles[ii], n=64)
            val[ii] = -(R/parameters.g)*v

    return val


# Get coordinates to pass to Dval function
W = VectorFunctionSpace(mesh, D0.ufl_element())
X = interpolate(mesh.coordinates, W)
D0.dat.data[:] = Dval(X.dat.data_ro)

# Adjust mean value of initial D
C = Function(D0.function_space()).assign(Constant(1.0))
area = assemble(C*dx)
Dmean = assemble(D0*dx)/area
D0 -= Dmean
D0 += Constant(parameters.H)

# optional perturbation
if perturb:
    alpha = Constant(1/3.)
    beta = Constant(1/15.)
    Dhat = Constant(120.)
    theta2 = Constant(pi/4.)
    g = Constant(parameters.g)
    D_pert = Function(D0.function_space()).interpolate(Dhat*cos(theta)*exp(-(lamda/alpha)**2)*exp(-((theta2 - theta)/beta)**2))
    D0 += D_pert

state.initialise([('u', u0), ('D', D0)])

ueqn = EulerPoincare(state, u0.function_space())
Deqn = AdvectionEquation(state, D0.function_space(), equation_form="continuity")
mass_flux = MassFluxReconstruction(state)
pv_flux = PVFluxTaylorGalerkin(state, mass_flux)

linear_solver = ShallowWaterSolver(state)

# Set up forcing
sw_forcing = ShallowWaterForcing(state, euler_poincare=False, mass_flux=mass_flux.flux, pv_flux=pv_flux.flux)

# build time stepper
stepper = FluxForm(state, linear_solver, sw_forcing, fluxes=[mass_flux, pv_flux])

stepper.run(t=0, tmax=tmax)