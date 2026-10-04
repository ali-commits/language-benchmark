"""Stop only recorded, correctly tagged benchmark resources; preserve data."""

import json
from pathlib import Path

from infrastructure.aws.deploy import aws

if __name__ == "__main__":
    s = json.loads(Path("artifacts/aws/deployment.json").read_text())
    identity = aws("sts", "get-caller-identity")
    if identity["Account"] != s["account"]:
        raise RuntimeError("AWS account mismatch")
    ids = sorted({node["id"] for node in s["nodes"].values()})
    instances = aws("ec2", "describe-instances", "--instance-ids", *ids)
    for reservation in instances["Reservations"]:
        for node in reservation["Instances"]:
            if {"Key": "Project", "Value": "language-benchmark"} not in node["Tags"]:
                raise RuntimeError("resource tag mismatch")
    # The caller must first stop the campaign gracefully and preserve its verified reset.
    aws("ec2", "stop-instances", "--instance-ids", *ids)
    db = s["rds"]["DBInstanceIdentifier"]
    record = aws("rds", "describe-db-instances", "--db-instance-identifier", db)["DBInstances"][0]
    if {"Key": "Project", "Value": "language-benchmark"} not in aws(
        "rds", "list-tags-for-resource", "--resource-name", record["DBInstanceArn"]
    )["TagList"]:
        raise RuntimeError("RDS tag mismatch")
    aws("rds", "stop-db-instance", "--db-instance-identifier", db)
    print("Stop requested. EBS/RDS storage remains billable; RDS restarts after seven days.")
