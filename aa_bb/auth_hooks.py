"""Hook into Alliance Auth"""

# Django
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook

# AA Example App
from aa_bb import urls, urls_loa, urls_cb, urls_paps
from .modelss import LeaveRequest


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


class BigBrotherManualMenuItem(MenuItemHook):
    """Menu entry for the BigBrother user manual."""

    def __init__(self):
        super().__init__(
            _("Big Brother Manual"),
            "fas fa-book",
            "aa_bb:manual",
            navactive=[
                "aa_bb:manual",
                "aa_bb:manual_cards",
                "aa_bb:manual_settings",
                "aa_bb:manual_settings_bb",
                "aa_bb:manual_settings_paps",
                "aa_bb:manual_settings_tickets",
                "aa_bb:manual_modules",
                "aa_bb:manual_faq",
            ],
        )

    def render(self, request):
        if request.user.has_perm("aa_bb.basic_access"):
            return super().render(request)
        return ""


@hooks.register("menu_item_hook")
def register_bigbrother_manual_menu():
    return BigBrotherManualMenuItem()


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
            if request.user.has_perm("aa_bb.can_view_all_loa"):
                pending_count = LeaveRequest.objects.filter(status="pending").count()
                if pending_count:
                    self.count = pending_count
                return MenuItemHook.render(self, request)
            return MenuItemHook.render(self, request)
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
            "fas fa-chart-bar",
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


@hooks.register('discord_cogs_hook')
def register_cogs():
    return ["aa_bb.tasks_bot"]
