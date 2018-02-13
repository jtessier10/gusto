from abc import ABCMeta, abstractmethod
from gusto.transport_equation import EmbeddedDGAdvection
from gusto.advection import SSPRK3
from gusto.limiters import ThetaLimiter
from gusto import thermodynamics
from firedrake import Projector, Interpolator, conditional, Function, \
    min_value, max_value, TestFunction, dx, as_vector, \
    NonlinearVariationalProblem, NonlinearVariationalSolver, Constant, pi
from scipy.special import gamma


__all__ = ["Condensation", "Fallout", "Coalescence", "Collection", "Autoconversion"]


class Physics(object, metaclass=ABCMeta):
    """
    Base class for physics processes for Gusto.

    :arg state: :class:`.State` object.
    """

    def __init__(self, state):
        self.state = state

    @abstractmethod
    def apply(self):
        """
        Function computes the value of specific
        fields at the next time step.
        """
        pass


class Condensation(Physics):
    """
    The process of condensation of water vapour
    into liquid water and evaporation of liquid
    water into water vapour, with the associated
    latent heat changes.

    :arg state: :class:`.State.` object.
    :arg weak: Boolean to determine whether weak
               formulation of condensation is used.
    """

    def __init__(self, state, weak=False):
        super(Condensation, self).__init__(state)

        self.weak = weak

        # obtain our fields
        self.theta = state.fields('theta')
        self.water_v = state.fields('water_v')
        self.water_c = state.fields('water_c')
        rho = state.fields('rho')

        # declare function space
        Vt = self.theta.function_space()

        # define some parameters as attributes
        dt = state.timestepping.dt
        R_d = state.parameters.R_d
        cp = state.parameters.cp
        cv = state.parameters.cv
        c_pv = state.parameters.c_pv
        c_pl = state.parameters.c_pl
        c_vv = state.parameters.c_vv
        R_v = state.parameters.R_v

        # make useful fields
        Pi = thermodynamics.pi(state.parameters, rho, self.theta)
        T = thermodynamics.T(state.parameters, self.theta, Pi, r_v=self.water_v)
        p = thermodynamics.p(state.parameters, Pi)
        L_v = thermodynamics.Lv(state.parameters, T)
        R_m = R_d + R_v * self.water_v
        c_pml = cp + c_pv * self.water_v + c_pl * self.water_c
        c_vml = cv + c_vv * self.water_v + c_pl * self.water_c

        # use Teten's formula to calculate w_sat
        w_sat = thermodynamics.r_sat(state.parameters, T, p)

        # make appropriate condensation rate
        dot_r_cond = ((self.water_v - w_sat) /
                      (dt * (1.0 + ((L_v ** 2.0 * w_sat) /
                                    (cp * R_v * T ** 2.0)))))

        # introduce a weak condensation which might hold at discrete level
        if self.weak:
            cond_rate_expr = dot_r_cond
            dot_r_cond = Function(Vt)
            phi = TestFunction(Vt)
            quadrature_degree = (4, 4)
            dxp = dx(degree=(quadrature_degree))
            cond_rate_functional = (phi * dot_r_cond * dxp
                                    - phi * cond_rate_expr * dxp)
            cond_rate_problem = NonlinearVariationalProblem(cond_rate_functional, dot_r_cond)
            self.cond_rate_solver = NonlinearVariationalSolver(cond_rate_problem)

        # make cond_rate function, that needs to be the same for all updates in one time step
        self.cond_rate = Function(Vt)

        # adjust cond rate so negative concentrations don't occur
        self.lim_cond_rate = Interpolator(conditional(dot_r_cond < 0,
                                                      max_value(dot_r_cond, - self.water_c / dt),
                                                      min_value(dot_r_cond, self.water_v / dt)), self.cond_rate)

        # tell the prognostic fields what to update to
        self.water_v_new = Interpolator(self.water_v - dt * self.cond_rate, Vt)
        self.water_c_new = Interpolator(self.water_c + dt * self.cond_rate, Vt)
        self.theta_new = Interpolator(self.theta *
                                      (1.0 + dt * self.cond_rate *
                                       (cv * L_v / (c_vml * cp * T) -
                                        R_v * cv * c_pml / (R_m * cp * c_vml))), Vt)

    def apply(self):
        if self.weak:
            self.cond_rate_solver.solve()
        self.lim_cond_rate.interpolate()
        self.theta.assign(self.theta_new.interpolate())
        self.water_v.assign(self.water_v_new.interpolate())
        self.water_c.assign(self.water_c_new.interpolate())


class Fallout(Physics):
    """
    The fallout process of hydrometeors.

    :arg state :class: `.State.` object.
    :arg moments: the moments of the distribution to be advected.
    """

    def __init__(self, state, moments=0):
        super(Fallout, self).__init__(state)

        self.rain = state.fields('rain')

        # function spaces
        Vt = self.rain.function_space()
        Vu = state.fields('u').function_space()

        # introduce sedimentation rate
        # for now assume all rain falls at terminal velocity
        terminal_velocity = 10  # in m/s
        self.v = state.fields("rainfall_velocity", Vu)

        if moments == 0:
            # all rain falls at same terminal velocity
            terminal_velocity = 10  # m/s
            v_expression = Constant(terminal_velocity)
        elif moments == 1:
            rho = state.fields('rho')
            water_l = 1000.0
            mass_threshold = 1e-10
            mu = 0
            N_r = 1e5
            c = pi * water_l / 6
            d = 3
            a = 362
            b = 0.65
            g = 0.5
            rho0 = 1.22
            Lambda0 = (N_r * c * gamma(1 + mu + d) / (gamma(1 + mu) * mass_threshold)) ** (1 / d)
            Lambda = (N_r * c * gamma(1 + mu + d) / (gamma(1 + mu) * self.rain)) ** (1 / d)
            v_expression = conditional(self.rain > mass_threshold,
                                       a * gamma(1 + mu + d + b) / (gamma(1 + mu + d) * Lambda ** b) * (rho0 / rho) ** g,
                                       a * gamma(1 + mu + d + b) / (gamma(1 + mu + d) * Lambda0 ** b) * (rho0 / rho) ** g)
        else:
            raise NotImplementedError('Currently we only have implementations for 0th and 1st moments of rainfall')
        solver_parameters = {'ksp_type': 'preonly', 'pc_type': 'lu', 'pc_factor_mat_solver_package': 'mumps'}
        self.determine_v = Projector(as_vector([0, -v_expression]), self.v, solver_parameters=solver_parameters)

        # sedimentation will happen using a full advection method
        advection_equation = EmbeddedDGAdvection(state, Vt, equation_form="advective", outflow=True)
        self.advection_method = SSPRK3(state, self.rain, advection_equation, limiter=ThetaLimiter(advection_equation))

    def apply(self):
        self.determine_v.project()
        self.advection_method.update_ubar(self.v, self.v, 0)
        self.advection_method.apply(self.rain, self.rain)


class Coalescence(Physics):
    """
    The process of coalescence of liquid water
    into rain droplets.
    :arg state: :class:`.State.` object.
    """

    def __init__(self, state):
        super(Coalescence, self).__init__(state)

        self.water_c = state.fields('water_c')
        self.rain = state.fields('rain')
        Vt = self.water_c.function_space()

        # constants
        dt = state.timestepping.dt
        k1 = 0.001
        k2 = 2.2
        a = 0.001

        # collection of rainwater
        collection = k2 * self.water_c * self.rain ** 0.875

        # autoconversion of cloud water into rain water
        autoconversion = k1 * (self.water_c - a)

        # make coalescence rate that will be the same function for all updates in one time step
        self.coalescence_rate = Function(Vt)

        # cap coalescence rate so that negative concentrations don't occur
        self.lim_coalescence_rate = Interpolator(conditional(collection + autoconversion < 0,
                                                             Constant(0.0),
                                                             min_value(collection + autoconversion, self.water_c / dt)),
                                                 self.coalescence_rate)

        # initiate updating of prognostic variables
        self.water_c_new = Interpolator(self.water_c - dt * self.coalescence_rate, Vt)
        self.rain_new = Interpolator(self.rain + dt * self.coalescence_rate, Vt)

    def apply(self):
        self.lim_coalescence_rate.interpolate()
        self.water_c.assign(self.water_c_new.interpolate())
        self.rain.assign(self.rain_new.interpolate())


class Collection(Physics):
    """
    The process of collection of liquid water
    into rain droplets.
    :arg state: :class:`.State.` object.
    """

    def __init__(self, state):
        super(Collection, self).__init__(state)

        self.water_c = state.fields('water_c')
        self.rain = state.fields('rain')
        Vt = self.water_c.function_space()

        # constants
        dt = state.timestepping.dt
        k2 = 2.2

        # collection of rainwater
        collection = k2 * self.water_c * self.rain ** 0.875

        # make coalescence rate that will be the same function for all updates in one time step
        self.collection_rate = Function(Vt)
        self.collection_projector = Projector(collection, self.collection_rate)
        self.limit_rate = Interpolator(conditional(self.collection_rate > 0,
                                                   min_value(self.collection_rate, self.water_c / dt),
                                                   0.0), self.collection_rate)

        # initiate updating of prognostic variables
        self.water_c_projector = Projector(self.water_c - dt * self.collection_rate, self.water_c)
        self.rain_projector = Projector(self.rain + dt * self.collection_rate, self.rain)

    def apply(self):
        self.collection_projector.project()
        self.water_c_projector.project()
        self.rain_projector.project()


class Autoconversion(Physics):
    """
    The process of collection of liquid water
    into rain droplets.
    :arg state: :class:`.State.` object.
    """

    def __init__(self, state):
        super(Autoconversion, self).__init__(state)

        self.water_c = state.fields('water_c')
        self.rain = state.fields('rain')
        Vt = self.water_c.function_space()

        # constants
        dt = state.timestepping.dt
        k1 = 0.001
        a = 0.001

        # collection of rainwater
        autoconversion = k1 * (self.water_c - a)

        # make coalescence rate that will be the same function for all updates in one time step
        self.autoconversion_rate = Function(Vt)
        self.autoconversion_projector = Projector(autoconversion, self.autoconversion_rate)
        self.limit_rate = Interpolator(conditional(dt * self.autoconversion_rate > 0,
                                                   min_value(self.autoconversion_rate, self.water_c / dt),
                                                   max_value(self.autoconversion_rate, - self.rain / dt)), self.autoconversion_rate)

        # initiate updating of prognostic variables
        self.water_c_projector = Projector(self.water_c - dt * self.autoconversion_rate, self.water_c)
        self.rain_projector = Projector(self.rain + dt * self.autoconversion_rate, self.rain)

    def apply(self):
        self.autoconversion_projector.project()
        self.limit_rate.interpolate()
        self.water_c_projector.project()
        self.rain_projector.project()
