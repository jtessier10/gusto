class State(object):
    """
    Build a model state to keep the variables in, and specify parameters.

    :arg mesh: The :class:`ExtrudedMesh` to use.
    :arg vertical_degree: integer, the degree for spaces in the vertical 
    (specifies the degree for the pressure space, other spaces are inferred)
    defaults to 1.
    :arg horizontal_degree: integer, the degree for spaces in the horizontal 
    (specifies the degree for the pressure space, other spaces are inferred)
    defaults to 1.
    :arg family: string, specifies the velocity space family to use. 
    Options:
    "RT": The Raviart-Thomas family (default, recommended for quads)
    "BDM": The BDM family
    "BDFM": The BDFM family
    :arg dt: the timestep
    :arg g: the acceleration due to gravity
    """

    def __init__(self, mesh, vertical_degree = 1, horizontal_degree = 1,
                 family = "RT",
                 dt = 1.0,
                 g = 9.81):
        
        #The mesh
        self.mesh = mesh

        #parameters
        self.dt = dt
        self.g = g
        
        #Build the spaces
        self._build_spaces(mesh, vertical_degree,
                          horizontal_degree, family)
        
        #Allocate state
        self._allocate_state()

    def initialise_state_from_expressions(self, u_expr, rho_expr, theta_expr):
        """
        Initialise state variables from expressions.
        :arg u_expr: This expression will be projected to initial u.
        :arg rho_expr: This expression will be interpolated to initial rho.
        :arg theta_expr: This expression will be interpolated to initial theta.
        """

        u_init, rho_init, theta_init = self.x_init.split()
        u_init.project(u_expr)
        rho_init.project(rho_expr)
        theta_init.project(theta_expr)

    def set_reference_profiles_from_expressions(self, rho_expr, theta_expr):
        """
        Initialise reference profiles from expressions.
        :arg rho_expr: This expression will be interpolated to rhoref.
        :arg theta_expr: This expression will be interpolated to thetaref.
        """

        self.rhoref = Function(self.V3)
        self.thetaref = Function(self.Vt)

        self.rho.project(rho_expr)
        self.theta.project(theta_expr)        

    def _build_spaces(self, mesh, vertical_degree, horizontal_degree, family):
        """
        Build:
        velocity space self.V2,
        pressure space self.V3,
        temperature space self.Vt,
        mixed function space self.W = (V2,V3,Vt)
        """

        #build spaces V2, V3, Vt
        raise(NotImplementedError)

        self.V2 = V2
        self.V3 = V3
        self.Vt = Vt
        self.W = MixedFunctionSpace((V2, V3, Vt))

    def _allocate_state(self):
        """
        Construct Functions to store the state variables.
        """

        self.xn = Function(W)
        self.x_init = Function(W)
        self.xstar = Function(W)
        self.xp = Function(W)
        self.xnp1 = Function(W)
        self.xrhs = Function(W)
        self.dy = Function(W)
