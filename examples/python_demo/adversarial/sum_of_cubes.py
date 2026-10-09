# Nonlinear integer reachability: d == 0 has a solution (Booker, 2019), but the
# smallest known one has 16-17 digit coordinates. A bounded solver budget must time out.
def cube_sum_ratio(x, y, z):
    d = x * x * x + y * y * y + z * z * z - 33
    return 100 / d
