from infrastructure.aws.deploy import build_plan


def test_deployment_preserves_backend_budget_and_isolates_database():
    p = build_plan("ami-test", "subnet-test", "ap-southeast-1c")
    assert p["nodes"]["backend"]["type"] == "c7i.large"
    assert p["nodes"]["load"]["type"] == "c7i.4xlarge"
    assert p["nodes"]["monitor"]["type"] == "c7i.large"
    assert p["rds"]["PubliclyAccessible"] is False
    assert p["rds"]["EngineVersion"] == "17.6"
    assert p["rds"]["BackupRetentionPeriod"] == 0
