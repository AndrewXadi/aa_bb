from allianceauth.authentication.models import CharacterOwnership

def get_user_character_names(user_id):
    """
    Given an Alliance Auth User ID, returns a comma-separated string
    of all character names linked to that user.
    """
    characters = CharacterOwnership.objects.filter(user__id=user_id)
    names =[]
    for char in characters:
        char_name = str(char.character)
        names.append(char_name)
    return ",".join(names)

def imp_bl(userID):
    return None