import django.contrib.postgres.constraints
import django.contrib.postgres.fields.ranges
import django.db.models.expressions
from django.contrib.postgres.operations import BtreeGistExtension
from django.db import migrations, models

import booking.models


class Migration(migrations.Migration):

    dependencies = [
        ("booking", "0001_initial"),
    ]

    operations = [
        # exclusion constraint 要對 staff_id（整數）用 = 運算子，需要 btree_gist
        BtreeGistExtension(),
        migrations.AddField(
            model_name="booking",
            name="price",
            field=models.PositiveIntegerField(default=0, verbose_name="價格（元）"),
            preserve_default=False,
        ),
        migrations.AddConstraint(
            model_name="booking",
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(
                condition=models.Q(("status__in", ("pending", "confirmed"))),
                expressions=[
                    (django.db.models.expressions.F("staff"), "="),
                    (
                        booking.models.TsTzRange(
                            "start_at", "end_at", django.contrib.postgres.fields.ranges.RangeBoundary()
                        ),
                        "&&",
                    ),
                ],
                name="booking_no_overlap_per_staff",
            ),
        ),
    ]
