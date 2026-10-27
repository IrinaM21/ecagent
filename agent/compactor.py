

class Compactor(Utils):
    def __init__(self):
        self.sys = """You are a context compactor tasked with compressing 
        the information in memory to avoid exceeding the token limit."""


    def compact_user_info(self,full_memory, i):
        # Compact user info from chat history

        # 1: model - identify salient points in existing memory
        prompt = f"""
        Given the current information in Memory,
        identify the salient points.
        <memory>{full_memory}</memory>
        Your response should be a numbered list of salient points, with each
        point on a new line.
        """
        
        salient = ""

        # 2: model - compress the salient points (incl. new input)
        prompt = f"""
        For each salient point in the given list, compress it so that it retains its meaning.
        Do the same for the new input to be added
        <salient points>{salient}</salient points>
        <new input>{i}</new input>
        Your response should be a numbered list of compressed salient points, 
        with each point on a new line. The compressed new input should be on its own line below,
        demarcated accordingly as 'Input: '
        """

        compressed = ""
        c_ls = []

        # 3: model - identify contradictions (incl. new input)
        prompt = f"""
        Given the list of compressed salient points and new input,
        identify any contradictions.
        <memory>{full_memory}</memory>
        Your response should be a numbered list of contradictions, represented by a 
        tuple (N, N) or (N, Input), where N is the number of the contradiction in the given
        numbered list, and Input indicates the input.
        """

        choices = "" # .split('\n')
        choice_ls = []
        for choice in choices:
            c = choice # TODO more parsing --> tuple
            a_idx = int(c[0]) if c[0] != 'Input' else -1
            b_idx = int(c[1]) if c[1] != 'Input' else -1
            # assuming no statement contradicts itself
            assert a_idx != b_idx
            choice_ls += [(c_ls[a_idx], c_ls[b_idx])]

        # 4: model - for each contradiction, decide + return which of the 2 is better

        chosen = [] # must be in order
        for choice in choice_ls:
            
            prompt = f"""
            For the given pair of contradicting statements, decide and return which of the
            2 statements should remain in the compressed memory. 
            <statement 1>{choice[0]}</statement 1>
            <statement 2>{choice[1]}</statement 2>
            Return only the text of the statement that should remain. Your choice:
            """

            # call model with prompt

        # 5: full_memory = chosen + new_input, maintain original order

        return full_memory # for now