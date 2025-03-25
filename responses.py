from random import choice, randint


def get_response(user_input: str) -> str:
    lowered: str = user_input.lower()

    if lowered == "":
        return "Hello guys."
    else:
        return "Hello guys."