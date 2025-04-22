from allianceauth.authentication.models import CharacterOwnership

def generate_blacklist_links(user_id, base_url="https://gice.goonfleet.com/Blacklist", max_url_length=2000):
    characters = CharacterOwnership.objects.filter(user__id=user_id)
    names = [str(char.character) for char in characters]
    
    links = []
    current_names = []

    for name in names:
        test_list = current_names + [name]
        query_string = ",".join(test_list)
        url = f"{base_url}?q={query_string}"

        if len(url) >= max_url_length:
            # Finalize the current URL and start a new batch
            link_label = "Click here" if not links else "and here"
            links.append(f"<a href='{base_url}?q={','.join(current_names)}'>{link_label}</a>")
            current_names = [name]
        else:
            current_names = test_list

    # Add the final batch if anything's left
    if current_names:
        link_label = "Click here" if not links else "and here"
        links.append(f"<a href='{base_url}?q={','.join(current_names)}'>{link_label}</a>")

    return links
