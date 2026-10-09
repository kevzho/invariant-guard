# The divisor does not depend on the unknown call, but the call may raise or never
# return, so a witness cannot be validated on this path.
def logged_inverse(a):
    audit_log(a)
    return 100 / a
