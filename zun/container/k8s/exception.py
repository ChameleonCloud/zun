from zun.common import exception

class K8sException(exception.ZunException):
    message = "Failed to perform operation on K8s"

class PutArchiveTimeout(K8sException):
    message = "Timed out after %(timeout)ss extracting into %(path)s"

class DirectoryCheckFailed(K8sException):
    message = "Could not tell whether %(path)s is a directory"

class PutArchiveFailed(K8sException):
    message = ("tar failed extracting into %(path)s "
               "(exit code %(exit_code)s): %(stderr)s")

class ReservationException(exception.Invalid):
    message = "Received invalid or missing reservation_id"
