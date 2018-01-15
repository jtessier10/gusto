"""
Some thermodynamic expressions to help declutter the code.
"""
from firedrake import exp, ln


__all__ = ["theta_expr", "pi_expr", "pi_rho_expr", "pi_theta_expr", "p_expr", "T_expr", "rho_expr", "r_sat_expr", "Lv_expr", "theta_e_expr", "I_expr", "RH_expr", "e_sat_expr", "r_v_expr", "T_d_expr"]


def theta_expr(parameters, T, p):
    """
    Returns an expression for dry potential temperature theta in K.

    :arg parameters: an OutputParameters object.
    :arg T: temperature in K.
    :arg p: pressure in Pa.
    """

    kappa = parameters.kappa
    p_0 = parameters.p_0

    return T * (p_0 / p) ** kappa


def pi_expr(parameters, rho, theta_v):
    """
    Returns an expression for the Exner pressure.

    :arg parameters: an OutputParameters object.
    :arg rho: the dry density of air in kg / m^3.
    :arg theta: the potential temperature (or the virtual
                potential temperature for wet air), in K.
    """

    kappa = parameters.kappa
    p_0 = parameters.p_0
    R_d = parameters.R_d

    return (rho * R_d * theta_v / p_0) ** (kappa / (1 - kappa))


def pi_rho_expr(parameters, rho, theta_v):
    """
    Returns an expression for the derivative of Exner pressure
    with respect to density.

    :arg parameters: an OutputParameters object.
    :arg rho: the dry density of air in kg / m^3.
    :arg theta: the potential temperature (or the virtual
                potential temperature for wet air), in K.
    """

    kappa = parameters.kappa
    p_0 = parameters.p_0
    R_d = parameters.R_d

    return (kappa / (1 - kappa)) * (rho * R_d * theta_v / p_0) ** (kappa / (1 - kappa)) / rho


def pi_theta_expr(parameters, rho, theta_v):
    """
    Returns an expression for the deriavtive of Exner pressure
    with respect to potential temperature.

    :arg parameters: an OutputParameters object.
    :arg rho: the dry density of air in kg / m^3.
    :arg theta: the potential temperature (or the virtual
                potential temperature for wet air), in K.
    """

    kappa = parameters.kappa
    p_0 = parameters.p_0
    R_d = parameters.R_d

    return (kappa / (1 - kappa)) * (rho * R_d * theta_v / p_0) ** (kappa / (1 - kappa)) / theta_v


def p_expr(parameters, pi):
    """
    Returns an expression for the pressure in Pa from the Exner Pi.

    :arg parameters: an OutputParameters object.
    :arg pi: the Exner pressure.
    """

    kappa = parameters.kappa
    p_0 = parameters.p_0

    return p_0 * pi ** (1 / kappa)


def T_expr(parameters, theta_v, pi, r_v=None):
    """
    Returns an expression for temperature T in K.

    :arg parameters: an OutputParameters object.
    :arg theta_v: the virtual potential temperature in K.
    :arg pi: the Exner pressure.
    :arg r_v: the mixing ratio of water vapour.
    """

    R_d = parameters.R_d
    R_v = parameters.R_v

    # if the air is wet, need to divide by (1 + r_v)
    if r_v is not None:
        return theta_v * pi / (1 + r_v * R_v / R_d)
    # in the case that r_v is None, theta_v=theta
    else:
        return theta_v * pi


def rho_expr(parameters, theta_v, pi):
    """
    Returns an expression for the dry density rho in kg / m^3
    from the (virtual) potential temperature and Exner pressure.

    :arg parameters: an OutputParameters object.
    :arg theta_v: the virtual potential temperature in K.
    :arg pi: the Exner pressure.
    """

    kappa = parameters.kappa
    p_0 = parameters.p_0
    R_d = parameters.R_d

    return p_0 * pi ** (1 / kappa - 1) / (R_d * theta_v)


def r_sat_expr(parameters, T, p):
    """
    Returns an expression from Tetens' formula for the
    saturation mixing ratio of water vapour.

    :arg parameters: an OutputParameters object.
    :arg T: the temperature in K.
    :arg p: the pressure in Pa.
    """

    w_sat1 = parameters.w_sat1
    w_sat2 = parameters.w_sat2
    w_sat3 = parameters.w_sat3
    w_sat4 = parameters.w_sat4
    T_0 = parameters.T_0

    return w_sat1 / (p * exp(w_sat2 * (T - T_0) / (T - w_sat3)) - w_sat4)


def Lv_expr(parameters, T):
    """
    Returns an expression for the latent heat of vaporisation of water.

    :arg parameters: an OutputParameters object.
    :arg T: the temperature in K.
    """

    L_v0 = parameters.L_v0
    T_0 = parameters.T_0
    c_pl = parameters.c_pl
    c_pv = parameters.c_pv

    return L_v0 - (c_pl - c_pv) * (T - T_0)


def theta_e_expr(parameters, T, p, r_v, r_t):
    """
    Returns an expression for the wet equivalent potential temperature in K.

    :arg parameters: an OutputParameters object.
    :arg T: the temperature in K.
    :arg p: the pressure in Pa.
    :arg r_v: the mixing ratio of water vapour.
    :arg r_t: the total mixing ratio of water.
    """

    R_d = parameters.R_d
    R_v = parameters.R_v
    p_0 = parameters.p_0
    cp = parameters.cp
    c_pl = parameters.c_pl
    Lv = Lv_expr(parameters, T)

    return T * (p_0 * (1 + r_v * R_v / R_d) / p) ** (R_d / (cp + c_pl * r_t)) * exp(Lv * r_v / (T * (cp + c_pl * r_t)))


def I_expr(parameters, rho, T, r_v=0.0, r_l=0.0):
    """
    Returns an expression for the (possibly wet) internal energy density in J.

    :arg parameters: an OutputParameters object.
    :arg rho: the dry density in kg / m^3.
    :arg T: the temperature in K.
    :arg r_v: the mixing ratio of water vapour.
    :arg r_l: the mixing ratio of all forms of liquid water.
    """

    cv = parameters.cv
    c_vv = parameters.c_vv
    c_pv = parameters.c_pv
    Lv = Lv_expr(parameters, T)

    return rho * (cv * T + r_v * c_vv * T + r_l * (c_pv * T - Lv))


def RH_expr(parameters, r_v, T, p):
    """
    Returns an expression for the relative humidity.

    :arg parameters: a CompressibleParameters object.
    :arg r_v: the mixing ratio of water vapour.
    :arg T: the temperature in K.
    :arg p: the pressure in Pa.
    """

    r_sat = r_sat_expr(parameters, T, p)

    return r_v / r_sat


def e_sat_expr(parameters, T):
    """
    Returns an expression for the saturated partial pressure
    of water vapour as a function of T, based on Tetens' formula.

    :arg parameters: a CompressibleParameters object.
    :arg T: the temperature in K.
    """

    w_sat2 = parameters.w_sat2
    w_sat3 = parameters.w_sat3
    w_sat4 = parameters.w_sat4
    T_0 = parameters.T_0

    return w_sat4 * exp(-w_sat2 * (T - T_0) / (T - w_sat3))


def e_expr(parameters, p, r_v):
    """
    Returns an expression for the partial pressure of water vapour
    from the total pressure and the water vapour mixing ratio.

    :arg parameters: a CompressibleParameters object.
    :arg p: the pressure in Pa.
    :arg r_v: the mixing ratio of water vapour.
    """

    epsilon = parameters.R_d / parameters.R_v

    return p * r_v / (epsilon + r_v)


def r_v_expr(parameters, H, T, p):
    """
    Returns an expression for the mixing ratio of water vapour
    from the relative humidity, pressure and temperature.

    :arg parameters: a CompressibleParameters object.
    :arg H: the relative humidity (as a decimal).
    :arg T: the temperature in K.
    :arg p: the pressure in Pa.
    """

    epsilon = parameters.R_d / parameters.R_v
    r_sat = r_sat_expr(parameters, T, p)

    return H * r_sat / (1 + (1 - H) * r_sat / epsilon)


def T_d_expr(parameters, p, r_v):
    """
    Returns the dewpoint temperature as a function of
    temperature and the water vapour mixing ratio.

    :arg parameters: a CompressibleParameters object.
    :arg T: the temperature in K.
    :arg r_v: the water vapour mixing ratio.
    """

    R_d = parameters.R_d
    R_v = parameters.R_v
    T_0 = parameters.T_0
    e = p * r_v / (r_v + R_d / R_v)

    return 243.5 / ((17.67 / ln(e / 611.2)) - 1) + T_0