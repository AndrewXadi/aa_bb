"""App Configuration"""

# Django
from django.apps import AppConfig

# aa-bb App
from aa_bb import __version__


class ExampleConfig(AppConfig):
    """App Config"""

    name = "aa_bb"
    label = "aa_bb"
    verbose_name = f"aa_bb v{__version__}"
