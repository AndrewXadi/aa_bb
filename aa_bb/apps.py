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

            task, created = PeriodicTask.objects.get_or_create(
                name="BB run regular updates",
                defaults={
                    "interval": schedule,
                    "task": "aa_bb.tasks.BB_run_regular_updates",
                    "enabled": False,  # only on creation
                },
            )

            if not created:
                updated = False
                if task.interval != schedule or task.task != "aa_bb.tasks.BB_run_regular_updates":
                    task.interval = schedule
                    task.task = "aa_bb.tasks.BB_run_regular_updates"
                    task.save()
                    updated = True
                if updated:
                    logger.info("✅ Updated ‘BB run regular updates’ periodic task")
                else:
                    logger.info("ℹ️ ‘BB run regular updates’ periodic task already exists and is up to date")
            else:
                logger.info("✅ Created ‘BB run regular updates’ periodic task with enabled=False")
        except (OperationalError, ProgrammingError) as e:
            logger.warning(f"Could not register periodic task yet: {e}")

