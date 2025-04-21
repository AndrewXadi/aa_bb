# aa_bb/apps.py

from django.apps import AppConfig
from django.db.utils import OperationalError, ProgrammingError

class AaBbConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "aa_bb"
    verbose_name = "aa_bb"

    def ready(self):
        import logging
        logger = logging.getLogger(__name__)
        logger.info("🟢 aa_bb.AaBbConfig.ready() fired!")

        try:
            from django_celery_beat.models import PeriodicTask, IntervalSchedule

            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=1,
                period=IntervalSchedule.HOURS,
            )
            PeriodicTask.objects.update_or_create(
                name="BB run regular updates",
                defaults={
                    "interval": schedule,
                    "task": "aa_bb.tasks.BB_run_regular_updates",
                    "enabled": False,
                },
            )
            logger.info("✅ Registered/updated ‘BB run regular updates’ periodic task")
        except (OperationalError, ProgrammingError) as e:
            # DB isn’t ready yet (e.g. during migrate), so skip
            logger.warning(f"Could not register periodic task yet: {e}")
