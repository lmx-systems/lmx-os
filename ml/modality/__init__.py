"""The modality model's harness: which machine could have made a delivery.

`docs/ROADMAP_1.5.md` DE-8 to DE-14. Standard library, like `ml/m1/`, so the
splits and the checklist baseline run where no ML stack is installed. Reads only
files the app exports - the machine limits (`scripts/machine_limits.py export`)
and the frozen test sites (`scripts/freeze_test_sites.py export`) - because
nothing here may import `app/`.
"""
