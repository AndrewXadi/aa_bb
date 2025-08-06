from django.apps import AppConfig
from django.db.utils import OperationalError, ProgrammingError

class AaBbConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "aa_bb"
    verbose_name = "aa_bb"

    def ready(self):
        import aa_bb.signals
        import logging
        logger = logging.getLogger(__name__)

        try:
            from django_celery_beat.models import PeriodicTask, IntervalSchedule, CrontabSchedule

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

            scheduleloa, _ = IntervalSchedule.objects.get_or_create(
                every=1,
                period=IntervalSchedule.HOURS,
            )

            task_loa, created_loa = PeriodicTask.objects.get_or_create(
                name="BB run regular LoA updates",
                defaults={
                    "interval": scheduleloa,
                    "task": "aa_bb.tasks.BB_run_regular_loa_updates",
                    "enabled": True,  # only on creation
                },
            )

            if not created_loa:
                updated_loa = False
                if task_loa.interval != scheduleloa or task_loa.task != "aa_bb.tasks.BB_run_regular_loa_updates":
                    task_loa.interval = scheduleloa
                    task_loa.task = "aa_bb.tasks.BB_run_regular_loa_updates"
                    task_loa.save()
                    updated_loa = True
                if updated_loa:
                    logger.info("✅ Updated ‘BB run regular LoA updates’ periodic task")
                else:
                    logger.info("ℹ️ ‘BB run regular LoA updates’ periodic task already exists and is up to date")
            else:
                logger.info("✅ Created ‘BB run regular LoA updates’ periodic task with enabled=False")




            # Daily messages
            from .models import BigBrotherConfig
            config = BigBrotherConfig.get_solo()

            # Default fallback schedule (12:00 UTC daily)
            default_schedule, _ = CrontabSchedule.objects.get_or_create(
                minute='0',
                hour='12',
                day_of_week='*',
                day_of_month='*',
                month_of_year='*',
                timezone='UTC',
            )

            # Tasks info: name, task path, config schedule attr, active flag attr
            tasks = [
                {
                    "name": "BB send daily message",
                    "task_path": "aa_bb.tasks.BB_send_daily_messages",
                    "schedule_attr": "dailyschedule",
                    "active_attr": "are_daily_messages_active",
                },
                {
                    "name": "BB send optional message 1",
                    "task_path": "aa_bb.tasks.BB_send_opt_message1",
                    "schedule_attr": "optschedule1",
                    "active_attr": "are_opt_messages1_active",
                },
                {
                    "name": "BB send optional message 2",
                    "task_path": "aa_bb.tasks.BB_send_opt_message2",
                    "schedule_attr": "optschedule2",
                    "active_attr": "are_opt_messages2_active",
                },
                {
                    "name": "BB send optional message 3",
                    "task_path": "aa_bb.tasks.BB_send_opt_message3",
                    "schedule_attr": "optschedule3",
                    "active_attr": "are_opt_messages3_active",
                },
                {
                    "name": "BB send optional message 4",
                    "task_path": "aa_bb.tasks.BB_send_opt_message4",
                    "schedule_attr": "optschedule4",
                    "active_attr": "are_opt_messages4_active",
                },
                {
                    "name": "BB send optional message 5",
                    "task_path": "aa_bb.tasks.BB_send_opt_message5",
                    "schedule_attr": "optschedule5",
                    "active_attr": "are_opt_messages5_active",
                },
            ]

            for task_info in tasks:
                name = task_info["name"]
                task_path = task_info["task_path"]
                schedule = getattr(config, task_info["schedule_attr"], None) or default_schedule
                is_active = getattr(config, task_info["active_attr"], False)

                existing_task = PeriodicTask.objects.filter(name=name).first()

                if is_active:
                    if existing_task is None:
                        # Create new periodic task
                        PeriodicTask.objects.create(
                            name=name,
                            task=task_path,
                            crontab=schedule,
                            enabled=True,
                        )
                        logger.info(f"✅ Created '{name}' periodic task with enabled=True")
                    else:
                        updated = False
                        if existing_task.crontab != schedule:
                            existing_task.crontab = schedule
                            updated = True
                        if existing_task.task != task_path:
                            existing_task.task = task_path
                            updated = True
                        if not existing_task.enabled:
                            existing_task.enabled = True
                            updated = True
                        if updated:
                            existing_task.save()
                            logger.info(f"✅ Updated '{name}' periodic task")
                        else:
                            logger.info(f"ℹ️ '{name}' periodic task already exists and is up to date")
                else:
                    # Not active - delete the task if exists
                    if existing_task:
                        existing_task.delete()
                        logger.info(f"🗑️ Deleted '{name}' periodic task because messages are disabled")
        except (OperationalError, ProgrammingError) as e:
            logger.warning(f"Could not register periodic task yet: {e}")



