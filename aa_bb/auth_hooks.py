"""Hook into Alliance Auth"""

# Django
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook

# AA Example App
from aa_bb import urls, urls_loa, urls_cb, urls_paps


class CorpBrotherMenuItem(MenuItemHook):
    """This class ensures only authorized users will see the menu entry"""

    def __init__(self):
        # setup menu entry for sidebar
        MenuItemHook.__init__(
            self,
            _("Corp Brother"),
            "fas fa-eye fa-fw",
            "aa_cb:index",
            navactive=["aa_cb:"],
        )

    def render(self, request):
        """Render the menu item"""

        if request.user.has_perm("aa_bb.basic_access_cb"):
            return MenuItemHook.render(self, request)

        return ""


@hooks.register("menu_item_hook")
def register_menu_cb():
    """Register the menu item"""

    return CorpBrotherMenuItem()


@hooks.register("url_hook")
def register_corpbrother_urls():
    return UrlHook(urls_cb, "CorpBrother", r"^aa_cb/")


class BigBrotherMenuItem(MenuItemHook):
    """This class ensures only authorized users will see the menu entry"""

    def __init__(self):
        # setup menu entry for sidebar
        MenuItemHook.__init__(
            self,
            _("Big Brother"),
            "fas fa-eye fa-fw",
            "aa_bb:index",
            navactive=["aa_bb:"],
        )

    def render(self, request):
        """Render the menu item"""

        if request.user.has_perm("aa_bb.basic_access"):
            return MenuItemHook.render(self, request)

        return ""


@hooks.register("menu_item_hook")
def register_menu():
    """Register the menu item"""

    return BigBrotherMenuItem()


@hooks.register("url_hook")
def register_bigbrother_urls():
    return UrlHook(urls, "BigBrother", r"^aa_bb/")


class LoAMenuItem(MenuItemHook):
    def __init__(self):
        super().__init__(
            _("Leave of Absence"),
            "fas fa-plane",
            "loa:index",
            navactive=["loa:"],
        )
    def render(self, request):
        # Optional permission check:
        # if not request.user.has_perm("aa_bb.can_access_loa"):
        #     return ""
        if request.user.has_perm("aa_bb.can_access_loa"):
            return super().render(request)
        return ""

@hooks.register("menu_item_hook")
def register_loa_menu():
    return LoAMenuItem()

@hooks.register("url_hook")
def register_loa_urls():
    return UrlHook(urls_loa, "loa", r"^loa/")


class PapsMenuItem(MenuItemHook):
    def __init__(self):
        super().__init__(
            _("PAP Stats"),
            "fas fa-plane",
            "paps:history",
            navactive=["paps:"],
        )
    def render(self, request):
        if request.user.has_perm("aa_bb.can_access_paps"):
            return super().render(request)
        return ""

@hooks.register("menu_item_hook")
def register_paps_menu():
    return PapsMenuItem()

@hooks.register("url_hook")
def register_paps_urls():
    return UrlHook(urls_paps, "paps", r"^paps/")
