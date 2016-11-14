from __future__ import absolute_import
from abc import ABCMeta, abstractmethod
from firedrake import Function, split, TrialFunction, TestFunction, \
    FacetNormal, inner, dx, cross, div, jump, avg, dS_v, \
    DirichletBC, LinearVariationalProblem, LinearVariationalSolver, \
    CellNormal, dot, dS, Constant


class Forcing(object):
    """
    Base class for forcing terms for Gusto.

    :arg state: x :class:`.State` object.
    """
    __metaclass__ = ABCMeta

    def __init__(self, state, **kwargs):
        self.state = state
        euler_poincare = kwargs.get('euler_poincare', True)
        linear = kwargs.get('linear', False)
        if linear:
            self.euler_poincare = False
        else:
            self.euler_poincare = euler_poincare

        self._build_forcing_solver()

    @abstractmethod
    def _build_forcing_solver(self):
        pass

    @abstractmethod
    def apply(self, scale, x, x_nl, x_out, **kwargs):
        """
        Function takes x as input, computes F(x_nl) and returns
        x_out = x + scale*F(x_nl)
        as output.

        :arg x: :class:`.Function` object
        :arg x_nl: :class:`.Function` object
        :arg x_out: :class:`.Function` object
        :arg mu_alpha: scale for sponge term, if present
        """
        pass


class CompressibleForcing(Forcing):
    """
    Forcing class for compressible Euler equations.
    """

    def _build_forcing_solver(self):
        """
        Only put forcing terms into the u equation.
        """

        state = self.state
        self.scaling = Constant(1.)
        Vu = state.V[0]
        W = state.W

        self.x0 = Function(W)   # copy x to here

        u0,rho0,theta0 = split(self.x0)

        F = TrialFunction(Vu)
        w = TestFunction(Vu)
        self.uF = Function(Vu)

        Omega = state.Omega
        cp = state.parameters.cp
        mu = state.mu

        n = FacetNormal(state.mesh)

        pi = exner(theta0, rho0, state)

        a = inner(w,F)*dx
        L = self.scaling*(
            + cp*div(theta0*w)*pi*dx  # pressure gradient [volume]
            - cp*jump(w*theta0,n)*avg(pi)*dS_v  # pressure gradient [surface]
        )

        if state.parameters.geopotential:
            Phi = state.Phi
            L += self.scaling*div(w)*Phi*dx  # gravity term
        else:
            g = state.parameters.g
            L -= self.scaling*g*inner(w,state.k)*dx  # gravity term

        if self.euler_poincare:
            L -= self.scaling*0.5*div(w)*inner(u0, u0)*dx

        if Omega is not None:
            u_init = state.x_init.split()[0]
            L -= self.scaling*inner(w,cross(2*Omega,u0-u_init))*dx  # Coriolis term

        if mu is not None:
            self.mu_scaling = Constant(1.)
            L -= self.mu_scaling*mu*inner(w,state.k)*inner(u0,state.k)*dx

        bcs = [DirichletBC(Vu, 0.0, "bottom"),
               DirichletBC(Vu, 0.0, "top")]

        u_forcing_problem = LinearVariationalProblem(
            a,L,self.uF, bcs=bcs
        )

        self.u_forcing_solver = LinearVariationalSolver(u_forcing_problem)

    def apply(self, scaling, x_in, x_nl, x_out, **kwargs):

        self.x0.assign(x_nl)
        self.scaling.assign(scaling)
        if 'mu_alpha' in kwargs and kwargs['mu_alpha'] is not None:
            self.mu_scaling.assign(kwargs['mu_alpha'])
        self.u_forcing_solver.solve()  # places forcing in self.uF

        u_out, _, _ = x_out.split()

        x_out.assign(x_in)
        u_out += self.uF


def exner(theta,rho,state):
    """
    Compute the exner function.
    """
    R_d = state.parameters.R_d
    p_0 = state.parameters.p_0
    kappa = state.parameters.kappa

    return (R_d/p_0)**(kappa/(1-kappa))*pow(rho*theta, kappa/(1-kappa))


def exner_rho(theta,rho,state):
    R_d = state.parameters.R_d
    p_0 = state.parameters.p_0
    kappa = state.parameters.kappa

    return (R_d/p_0)**(kappa/(1-kappa))*pow(rho*theta, kappa/(1-kappa)-1)*theta*kappa/(1-kappa)


def exner_theta(theta,rho,state):
    R_d = state.parameters.R_d
    p_0 = state.parameters.p_0
    kappa = state.parameters.kappa

    return (R_d/p_0)**(kappa/(1-kappa))*pow(rho*theta, kappa/(1-kappa)-1)*rho*kappa/(1-kappa)


class IncompressibleForcing(Forcing):
    """
    Forcing class for incompressible Euler Boussinesq equations.
    """

    def _build_forcing_solver(self):
        """
        Only put forcing terms into the u equation.
        """

        state = self.state
        self.scaling = Constant(1.)
        Vu = state.V[0]
        W = state.W

        self.x0 = Function(W)   # copy x to here

        u0,p0,b0 = split(self.x0)

        F = TrialFunction(Vu)
        w = TestFunction(Vu)
        self.uF = Function(Vu)

        Omega = state.Omega
        mu = state.mu

        a = inner(w,F)*dx
        L = (
            self.scaling*div(w)*p0*dx  # pressure gradient
            + self.scaling*b0*inner(w,state.k)*dx  # gravity term
        )

        if self.euler_poincare:
            L -= self.scaling*0.5*div(w)*inner(u0, u0)*dx

        if Omega is not None:
            L -= self.scaling*inner(w,cross(2*Omega,u0))*dx  # Coriolis term

        if mu is not None:
            self.mu_scaling = Constant(1.)
            L -= self.mu_scaling*mu*inner(w,state.k)*inner(u0,state.k)*dx

        bcs = [DirichletBC(Vu, 0.0, "bottom"),
               DirichletBC(Vu, 0.0, "top")]

        u_forcing_problem = LinearVariationalProblem(
            a,L,self.uF, bcs=bcs
        )

        self.u_forcing_solver = LinearVariationalSolver(u_forcing_problem)

        Vp = state.V[1]
        p = TrialFunction(Vp)
        q = TestFunction(Vp)
        self.divu = Function(Vp)

        a = p*q*dx
        L = q*div(u0)*dx

        divergence_problem = LinearVariationalProblem(
            a, L, self.divu)

        self.divergence_solver = LinearVariationalSolver(divergence_problem)

    def apply(self, scaling, x_in, x_nl, x_out, **kwargs):

        self.x0.assign(x_nl)
        self.scaling.assign(scaling)
        if 'mu_alpha' in kwargs and kwargs['mu_alpha'] is not None:
            self.mu_scaling.assign(kwargs['mu_alpha'])
        self.u_forcing_solver.solve()  # places forcing in self.uF

        u_out, p_out, _ = x_out.split()

        x_out.assign(x_in)
        u_out += self.uF

        if 'incompressible' in kwargs and kwargs['incompressible']:
            self.divergence_solver.solve()
            p_out.assign(self.divu)


class ShallowWaterForcing(Forcing):

    def _build_forcing_solver(self):

        state = self.state
        g = state.parameters.g
        f = state.f

        Vu = state.V[0]
        W = state.W

        self.x0 = Function(W)   # copy x to here

        u0, D0 = split(self.x0)
        n = FacetNormal(state.mesh)
        un = 0.5*(dot(u0, n) + abs(dot(u0, n)))

        F = TrialFunction(Vu)
        w = TestFunction(Vu)
        self.uF = Function(Vu)

        outward_normals = CellNormal(state.mesh)
        perp = lambda u: cross(outward_normals, u)
        a = inner(w, F)*dx
        L = (
            (-f*inner(w, perp(u0)) + g*div(w)*D0)*dx
            - g*inner(jump(w, n), un('+')*D0('+') - un('-')*D0('-'))*dS)

        if self.euler_poincare:
            L -= 0.5*div(w)*inner(u0, u0)*dx

        u_forcing_problem = LinearVariationalProblem(
            a, L, self.uF)

        self.u_forcing_solver = LinearVariationalSolver(u_forcing_problem)

    def apply(self, scaling, x_in, x_nl, x_out, **kwargs):

        self.x0.assign(x_nl)

        self.u_forcing_solver.solve()  # places forcing in self.uF
        self.uF *= scaling

        uF, _ = x_out.split()

        x_out.assign(x_in)
        uF += self.uF
