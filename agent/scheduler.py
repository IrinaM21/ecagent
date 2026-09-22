from agent import ECAgent
# TODO change schedule() from testing placeholder

class Scheduler:
    # change later to reflect proposal
    def __init__(self, key):
        self.key = key
    
    # placeholder for testing eval
    def schedule(self, profile, calendar_info, event_info):
        a = ECAgent()
        prompt = f"""
        You are a scheduler. Determine the most reasonable time the given event 
        can occur, given the user's profile and schedule.

        <event>{event_info}</event>
        <profile>{profile}</profile>
        <schedule>{calendar_info}</schedule>

        Return ONLY the event name, and its corresponding best date (day, month, year) and time (hour, minute) 
        in valid JSON form, e.g.: {{"event":"RSO Meeting","day":"Monday", "month":"December" ....}}.
        """
        a = ECAgent(key)
        return a.generate_response(prompt)



