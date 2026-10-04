"""Provision only this experiment's tagged resources through AWS CLI."""

import json
import os
import secrets
import subprocess
import time
from pathlib import Path

REGION = "ap-southeast-1"
ROOT = Path("artifacts/aws")
STATE = ROOT / "deployment.json"
TAG = [{"Key": "Project", "Value": "language-benchmark"}, {"Key": "ManagedBy", "Value": "codex"}]


def build_plan(ami, subnet, az):
    return {
        "region": REGION,
        "ami": ami,
        "subnet": subnet,
        "az": az,
        "nodes": {
            "backend": {"type": "c7i.large", "disk": 60},
            "load": {"type": "c7i.4xlarge", "disk": 60},
            "monitor": {"type": "c7i.large", "disk": 100},
        },
        "rds": {
            "DBInstanceIdentifier": "language-benchmark",
            "DBName": "language_benchmark",
            "DBInstanceClass": "db.m7i.xlarge",
            "Engine": "postgres",
            "EngineVersion": "17.6",
            "AllocatedStorage": 100,
            "StorageType": "gp3",
            "StorageEncrypted": True,
            "PubliclyAccessible": False,
            "MultiAZ": False,
            "AvailabilityZone": az,
            "BackupRetentionPeriod": 0,
            "AutoMinorVersionUpgrade": False,
            "DeletionProtection": False,
        },
    }


def aws(*args, region=REGION):
    r = subprocess.run(
        ["aws", "--region", region, *map(str, args), "--output", "json"],
        capture_output=True,
        text=True,
    )
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return json.loads(r.stdout) if r.stdout.strip() else {}


def private_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)


def save(state):
    STATE.write_text(json.dumps(state, indent=2) + "\n")


def role(name, monitor=False):
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        aws("iam", "get-role", "--role-name", name)
    except RuntimeError as e:
        if "NoSuchEntity" not in str(e):
            raise
        aws(
            "iam",
            "create-role",
            "--role-name",
            name,
            "--assume-role-policy-document",
            json.dumps(trust),
            "--tags",
            json.dumps(TAG),
        )
    aws(
        "iam",
        "attach-role-policy",
        "--role-name",
        name,
        "--policy-arn",
        "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
    )
    if monitor:
        for policy in ["CloudWatchReadOnlyAccess", "AmazonRDSReadOnlyAccess"]:
            aws(
                "iam",
                "attach-role-policy",
                "--role-name",
                name,
                "--policy-arn",
                "arn:aws:iam::aws:policy/" + policy,
            )
    try:
        aws("iam", "get-instance-profile", "--instance-profile-name", name)
    except RuntimeError as e:
        if "NoSuchEntity" not in str(e):
            raise
        aws(
            "iam",
            "create-instance-profile",
            "--instance-profile-name",
            name,
            "--tags",
            json.dumps(TAG),
        )
        aws(
            "iam",
            "add-role-to-instance-profile",
            "--instance-profile-name",
            name,
            "--role-name",
            name,
        )
    return name


def provision():
    ROOT.mkdir(parents=True, exist_ok=True)
    ROOT.chmod(0o700)
    state = (
        json.loads(STATE.read_text())
        if STATE.exists()
        else build_plan("ami-0ffae9a3798ad85be", "subnet-00892d91138eeba09", "ap-southeast-1c")
    )
    state["account"] = aws("sts", "get-caller-identity")["Account"]
    state["vpc"] = "vpc-02f5fb70c6e6ac314"
    state["status"] = "provisioning"
    save(state)
    secret_file = ROOT / "secrets.json"
    if not secret_file.exists():
        private_json(
            secret_file,
            {
                "rds_password": secrets.token_urlsafe(36),
                "app_password": secrets.token_urlsafe(36),
                "jwt_secret": secrets.token_hex(32),
                "grafana_password": secrets.token_urlsafe(24),
            },
        )
    secret = json.loads(secret_file.read_text())
    if "keypair" not in state:
        name = "language-benchmark-20261004"
        data = aws(
            "ec2",
            "create-key-pair",
            "--key-name",
            name,
            "--key-type",
            "ed25519",
            "--tag-specifications",
            json.dumps([{"ResourceType": "key-pair", "Tags": TAG}]),
        )
        fd = os.open(ROOT / "ssh-key", os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(data["KeyMaterial"])
        state["keypair"] = name
        save(state)
    profiles = {
        "node": role("LanguageBenchmarkNode"),
        "monitor": role("LanguageBenchmarkMonitor", True),
    }
    state["profiles"] = profiles
    save(state)
    import urllib.request

    admin = urllib.request.urlopen("https://checkip.amazonaws.com").read().decode().strip() + "/32"
    state["admin_cidr"] = admin
    groups = state.setdefault("security_groups", {})
    for name in ["backend", "load", "monitor", "database"]:
        if name not in groups:
            g = aws(
                "ec2",
                "create-security-group",
                "--group-name",
                "language-benchmark-" + name,
                "--description",
                "Dedicated language benchmark " + name,
                "--vpc-id",
                state["vpc"],
                "--tag-specifications",
                json.dumps([{"ResourceType": "security-group", "Tags": TAG}]),
            )["GroupId"]
            groups[name] = g
            save(state)

    def ingress(group, port, source_group=None, cidr=None):
        rule = {"IpProtocol": "tcp", "FromPort": port, "ToPort": port}
        if source_group:
            rule["UserIdGroupPairs"] = [{"GroupId": source_group}]
        else:
            rule["IpRanges"] = [{"CidrIp": cidr}]
        try:
            aws(
                "ec2",
                "authorize-security-group-ingress",
                "--group-id",
                group,
                "--ip-permissions",
                json.dumps([rule]),
            )
        except RuntimeError as e:
            if "InvalidPermission.Duplicate" not in str(e):
                raise

    for name in ["backend", "load", "monitor"]:
        ingress(groups[name], 22, cidr=admin)
    for name in ["backend", "load"]:
        ingress(groups[name], 22, source_group=groups["monitor"])
        ingress(groups[name], 9100, source_group=groups["monitor"])
    ingress(groups["backend"], 8000, source_group=groups["load"])
    ingress(groups["backend"], 8000, source_group=groups["monitor"])
    for name in ["backend", "monitor"]:
        ingress(groups["database"], 5432, source_group=groups[name])
    ingress(groups["monitor"], 9090, source_group=groups["load"])
    # UI is loopback-only and reached by SSH tunnel; no public Grafana/Prometheus ingress.
    subnet_name = "language-benchmark"
    try:
        aws("rds", "describe-db-subnet-groups", "--db-subnet-group-name", subnet_name)
    except RuntimeError as e:
        if "DBSubnetGroupNotFound" not in str(e):
            raise
        aws(
            "rds",
            "create-db-subnet-group",
            "--db-subnet-group-name",
            subnet_name,
            "--db-subnet-group-description",
            "Isolated benchmark RDS placement",
            "--subnet-ids",
            "subnet-00892d91138eeba09",
            "subnet-045cf8cf22e0c40a3",
            "subnet-0fca594e9a1b84394",
            "--tags",
            json.dumps(TAG),
        )
    try:
        aws("rds", "describe-db-instances", "--db-instance-identifier", "language-benchmark")
    except RuntimeError as e:
        if "DBInstanceNotFound" not in str(e):
            raise
        request = {
            **state["rds"],
            "MasterUsername": "benchmark_admin",
            "MasterUserPassword": secret["rds_password"],
            "DBSubnetGroupName": subnet_name,
            "VpcSecurityGroupIds": [groups["database"]],
            "Tags": TAG,
        }
        private_json(ROOT / "rds-request.json", request)
        aws(
            "rds",
            "create-db-instance",
            "--cli-input-json",
            "file://" + str((ROOT / "rds-request.json").resolve()),
        )
    time.sleep(10)
    for name, config in state["nodes"].items():
        if config.get("id"):
            continue
        request = {
            "ImageId": state["ami"],
            "InstanceType": config["type"],
            "MinCount": 1,
            "MaxCount": 1,
            "KeyName": state["keypair"],
            "SubnetId": state["subnet"],
            "SecurityGroupIds": [groups[name]],
            "IamInstanceProfile": {"Name": profiles["monitor" if name == "monitor" else "node"]},
            "MetadataOptions": {"HttpTokens": "required", "HttpPutResponseHopLimit": 2},
            "BlockDeviceMappings": [
                {
                    "DeviceName": "/dev/sda1",
                    "Ebs": {
                        "VolumeSize": config["disk"],
                        "VolumeType": "gp3",
                        "Encrypted": True,
                        "DeleteOnTermination": True,
                    },
                }
            ],
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": TAG
                    + [
                        {"Key": "Name", "Value": "language-benchmark-" + name},
                        {"Key": "Role", "Value": name},
                    ],
                },
                {"ResourceType": "volume", "Tags": TAG + [{"Key": "Role", "Value": name}]},
            ],
            "ClientToken": "langbench-20261004-" + name,
        }
        request["UserData"] = (
            __import__("base64")
            .b64encode(Path("infrastructure/aws/bootstrap-rocky.sh").read_bytes())
            .decode()
        )
        private_json(ROOT / (name + "-request.json"), request)
        i = aws(
            "ec2",
            "run-instances",
            "--cli-input-json",
            "file://" + str((ROOT / (name + "-request.json")).resolve()),
        )["Instances"][0]
        config.update(id=i["InstanceId"], private_ip=i["PrivateIpAddress"])
        save(state)
        print(name, i["InstanceId"], flush=True)
    state["status"] = "bootstrapping"
    save(state)
    print("Provisioning initiated; state:", STATE)


if __name__ == "__main__":
    provision()
