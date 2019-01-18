from enum import Enum
from firedrake import (Function, TestFunction, TestFunctions, FacetNormal,
                       dx, dot, grad, div, jump, avg, dS, dS_v, dS_h, inner,
                       ds, ds_v, ds_t, ds_b,
                       outer, sign, cross, CellNormal,
                       curl, Constant)
from gusto.form_manipulation_labelling import advection, advecting_velocity, subject


__all__ = ["IntegrateByParts", "advection_form", "continuity_form"]


class IntegrateByParts(Enum):
    NEVER = 0
    ONCE = 1
    TWICE = 2


def is_cg(V):
    # find out if we are CG
    nvertex = V.ufl_domain().ufl_cell().num_vertices()
    entity_dofs = V.finat_element.entity_dofs()
    # If there are as many dofs on vertices as there are vertices,
    # assume a continuous space.
    try:
        return sum(map(len, entity_dofs[0].values())) == nvertex
    except KeyError:
        return sum(map(len, entity_dofs[(0, 0)].values())) == nvertex


def surface_measures(V, direction=None):
    if is_cg(V):
        return None, None
    else:
        if V.extruded:
            return (dS_h + dS_v), (ds_b + ds_t + ds_v)
        else:
            return dS, ds


def advection_form(state, V, idx, *, ibp=IntegrateByParts.ONCE, outflow=None):
    """
    The equation is assumed to be in the form:

    q_t + L(q) = 0

    where q is the (scalar or vector) field to be solved for.

    :arg state: :class:`.State` object.
    :arg V: :class:`.FunctionSpace object. The function space that q lives in.
    :arg ibp: string, stands for 'integrate by parts' and can take the value
              None, "once" or "twice". Defaults to "once".
    """
    X = Function(V)
    if len(V) > 1:
        test = TestFunctions(V)[idx]
        q = X.split()[idx]
        ubar = Function(V.sub(0))
    else:
        test = TestFunction(V)
        q = X
        ubar = Function(state.spaces("HDiv"))

    dS, ds = surface_measures(q.function_space())

    if ibp == IntegrateByParts.ONCE:
        L = -inner(div(outer(test, ubar)), q)*dx
    else:
        L = inner(outer(test, ubar), grad(q))*dx

    if dS is not None and ibp != IntegrateByParts.NEVER:
        n = FacetNormal(state.mesh)
        un = 0.5*(dot(ubar, n) + abs(dot(ubar, n)))

        L += dot(jump(test), (un('+')*q('+') - un('-')*q('-')))*dS

        if ibp == IntegrateByParts.TWICE:
            L -= (inner(test('+'), dot(ubar('+'), n('+'))*q('+'))
                  + inner(test('-'), dot(ubar('-'), n('-'))*q('-')))*dS

    if outflow is not None:
        L += test*un*q*ds

    form = subject(advection(advecting_velocity(L, ubar)), X)
    return form


def linear_advection_form(state, V, idx, qbar):

    X = Function(V)
    if len(V) > 1:
        test = TestFunctions(V)[idx]
        ubar = Function(V.sub(0))
    else:
        test = TestFunction(V)
        ubar = Function(state.spaces("HDiv"))

    form = subject(advection(advecting_velocity(Constant(qbar)*test*div(ubar)*dx, ubar)), X)
    return form


def continuity_form(state, V, idx, *, ibp=IntegrateByParts.ONCE):

    X = Function(V)
    if len(V) > 1:
        test = TestFunctions(V)[idx]
        q = X.split()[idx]
        ubar = Function(V.sub(0))
    else:
        test = TestFunction(V)
        q = X
        ubar = Function(state.spaces("HDiv"))

    dS, ds = surface_measures(q.function_space())

    if ibp == IntegrateByParts.ONCE:
        L = -inner(grad(test), outer(q, ubar))*dx
    else:
        L = inner(test, div(outer(q, ubar)))*dx

    if dS is not None and ibp != IntegrateByParts.NEVER:
        n = FacetNormal(state.mesh)
        un = 0.5*(dot(ubar, n) + abs(dot(ubar, n)))

        L += dot(jump(test), (un('+')*q('+') - un('-')*q('-')))*dS

        if ibp == IntegrateByParts.TWICE:
            L -= (inner(test('+'), dot(ubar('+'), n('+'))*q('+'))
                  + inner(test('-'), dot(ubar('-'), n('-'))*q('-')))*dS

    form = subject(advection(advecting_velocity(L, ubar)), X)
    return form


def advection_vector_manifold_form(state, V, idx, *, ibp=IntegrateByParts.ONCE, outflow=None):
    X = Function(V)
    if len(V) > 1:
        test = TestFunctions(V)[idx]
        q = X.split()[idx]
        ubar = Function(V.sub(0))
    else:
        test = TestFunction(V)
        q = X
        ubar = Function(state.spaces("HDiv"))

    n = FacetNormal(state.mesh)
    un = 0.5*(dot(ubar, n) + abs(dot(ubar, n)))

    dS, ds = surface_measures(q.function_space())

    L = un('+')*inner(test('-'), n('+')+n('-'))*inner(q('+'), n('+'))*dS
    L += un('-')*inner(test('+'), n('+')+n('-'))*inner(q('-'), n('-'))*dS

    form = advection_form(state, V, idx, ibp=ibp) + subject(advection(advecting_velocity(L, ubar)), X)
    return form


def vector_invariant_form(state, V, idx, *, ibp=IntegrateByParts.ONCE):
    """
    Defines the vector invariant form of the vector advection term.

    :arg state: :class:`.State` object.
    :arg V: Function space
    :arg ibp: (optional) string, stands for 'integrate by parts' and can
              take the value None, "once" or "twice". Defaults to "once".
    """
    X = Function(V)
    if len(V) > 1:
        test = TestFunctions(V)[idx]
        q = X.split()[idx]
        ubar = Function(V.sub(0))
    else:
        test = TestFunction(V)
        q = X
        ubar = Function(state.spaces("HDiv"))

    dS, ds = surface_measures(q.function_space())

    n = FacetNormal(state.mesh)
    Upwind = 0.5*(sign(dot(ubar, n))+1)

    if state.mesh.topological_dimension() == 3:
        if ibp != IntegrateByParts.ONCE:
            raise NotImplementedError

        # <w,curl(u) cross ubar + grad( u.ubar)>
        # =<curl(u),ubar cross w> - <div(w), u.ubar>
        # =<u,curl(ubar cross w)> -
        #      <<u_upwind, [[n cross(ubar cross w)cross]]>>

        both = lambda u: 2*avg(u)

        L = (
            inner(q, curl(cross(ubar, test)))*dx
            - inner(both(Upwind*q),
                    both(cross(n, cross(ubar, test))))*dS
        )

    else:

        perp = state.perp
        if state.on_sphere:
            outward_normals = CellNormal(state.mesh)
            perp_u_upwind = lambda q: Upwind('+')*cross(outward_normals('+'), q('+')) + Upwind('-')*cross(outward_normals('-'), q('-'))
        else:
            perp_u_upwind = lambda q: Upwind('+')*perp(q('+')) + Upwind('-')*perp(q('-'))
        gradperp = lambda u: perp(grad(u))

        if ibp == IntegrateByParts.ONCE:
            L = (
                -inner(gradperp(inner(test, perp(ubar))), q)*dx
                - inner(jump(inner(test, perp(ubar)), n),
                        perp_u_upwind(q))*dS
            )
        else:
            L = (
                (-inner(test, div(perp(q))*perp(ubar)))*dx
                - inner(jump(inner(test, perp(ubar)), n),
                        perp_u_upwind(q))*dS
                + jump(inner(test, perp(ubar))*perp(q), n)*dS
            )

    L -= 0.5*div(test)*inner(q, ubar)*dx
    form = subject(advection(advecting_velocity(L, ubar)), X)
    return form


def kinetic_energy_form(state, V, idx):
    X = Function(V)
    if len(V) > 1:
        test = TestFunctions(V)[idx]
        q = X.split()[idx]
        ubar = Function(V.sub(0))
    else:
        test = TestFunction(V)
        q = X
        ubar = Function(state.spaces("HDiv"))

    form = subject(advection(advecting_velocity(0.5*div(test)*inner(q, ubar)*dx, ubar)), X)
    return form


def advection_equation_circulation_form(state, V, idx, *, ibp=IntegrateByParts.ONCE):
    """
    Defining the circulation form of the vector advection term.

    :arg state: :class:`.State` object.
    :arg V: Function space
    :arg ibp: string, stands for 'integrate by parts' and can take the value
              None, "once" or "twice". Defaults to "once".
    """
    form = vector_invariant_form(state, V, idx, ibp=ibp) - kinetic_energy_form(state, V, idx)
    return form
