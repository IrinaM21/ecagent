# registration tools for task 9 (every try is logged, so the grader can see the retry)

from utils import Logged


class RegistrationError(RuntimeError):
    pass


# same activity -> same key
def key(activity):
    return (activity.get("source"), activity.get("id") or activity.get("title"), activity.get("date"))


# fake registration for eval: fail_on={1} makes the first try fail
class SimulatedRegistration(Logged):
    def __init__(self, fail_on=()):
        self.fail_on = set(fail_on)
        self.calls = 0
        self.registered = {}

    # confirmation number, or raises if this call is set to fail
    def register(self, activity):
        self.calls += 1
        if self.calls in self.fail_on:
            self.log("register", ok=False, call=self.calls, title=activity.get("title"), error="temporary server error")
            raise RegistrationError("temporary server error, please try again")
        confirmation = self.registered.get(key(activity)) or f"REG-{len(self.registered) + 1:04d}"
        self.registered[key(activity)] = confirmation
        self.log("register", ok=True, call=self.calls, title=activity.get("title"), confirmation=confirmation)
        return {"status": "registered", "confirmation": confirmation}

    def is_registered(self, activity):
        return key(activity) in self.registered


# real use: there's no campus registration API, so we just give the link (never say they're registered)
class ManualRegistration(Logged):
    def register(self, activity):
        self.log("register", ok=True, status="link", title=activity.get("title"), url=activity.get("url"))
        return {"status": "link", "url": activity.get("url")}

    def is_registered(self, activity):
        return False
