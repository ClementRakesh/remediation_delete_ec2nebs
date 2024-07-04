import json
from pickle import NONE
import boto3
import datetime


def lambda_handler(event, context):
    try:
        ec2_client = boto3.client('ec2')

        # check if any EC2 instances are stopped
        stopped_instances = get_stopped_instances(ec2_client)
        if not stopped_instances:
            return success_empty_response()

        # check fo unused instances (checking for last 1 min to test the code, can be made configurable)
        unused_instances = get_instances_with_no_usage(stopped_instances)
        if not unused_instances:
            return success_empty_response()

        # if not in use, terminate it
        instance_ids = [instance.get('InstanceId')
                        for instance in unused_instances]
        ec2_client.terminate_instances(InstanceIds=instance_ids)

        # delete the attached EBS volumes
        terminate_attached_ebs_volumes(ec2_client, unused_instances)
        message = "EC2 Instances and EBS volumes were deleted successfully"
        error_message = ""
    except Exception as e:
        instance_ids = []
        message = "An error occurred while processing the request"
        error_message = str(e)
    return {
        "statusCode": 200,
        "body": json.dumps({
            "Message": message,
            "Data": instance_ids,
            "Error": error_message
        }, default=str)
    }


def get_stopped_instances(ec2_client):
    next_token = None
    result = []

    while True:
        params = {
            'Filters': [
                {'Name': 'instance-state-name', 'Values': ['stopped']}
            ],
            'MaxResults': 10
        }
        if next_token:
            params['NextToken'] = next_token

        response = ec2_client.describe_instances(**params)

        reservations = response.get("Reservations", [])
        for reservation in reservations:
            instances = reservation.get("Instances", [])
            result.extend(instances)

        next_token = response.get("NextToken")
        if not next_token:
            break

    return result


def get_instances_with_no_usage(instances):
    instances_with_no_usage = []
    end_date = datetime.datetime.now(datetime.timezone.utc)
    start_date = end_date - datetime.timedelta(minutes=1)
    cloudwatch = boto3.client('cloudwatch')

    for instance in instances:
        metrics = cloudwatch.get_metric_statistics(
            Namespace='AWS/EC2',
            MetricName='CPUUtilization',
            Dimensions=[
                {
                    'Name': 'InstanceId',
                    'Value': instance.get("InstanceId")
                },
            ],
            StartTime=start_date,
            EndTime=end_date,
            Period=3600,
            Statistics=[
                'Maximum',
            ],
            Unit='Percent'
        )
        data_points = metrics.get("Datapoints", [])
        if not data_points or any(round(float(data_point.get("Maximum", 0)), 2) == 0.0 for data_point in data_points):
            instances_with_no_usage.append(instance)

    return instances_with_no_usage


def terminate_attached_ebs_volumes(ec2_client, instances):
    volume_ids = [
        block_device_mapping['Ebs']['VolumeId']
        for instance in instances
        for block_device_mapping in instance.get('BlockDeviceMappings', [])
        if 'Ebs' in block_device_mapping and 'VolumeId' in block_device_mapping['Ebs']
    ]

    if volume_ids:
        for volume_id in volume_ids:
            if volumes := get_volume(ec2_client, volume_id):
                detach_volume(ec2_client, volume_id, volumes)
                ec2_client.delete_volume(VolumeId=volume_id)


def detach_volume(ec2_client, volume_id, volumes):
    try:
        for volume in volumes:
            if (volume["State"] == "in-use"):
                ec2_client.detach_volume(
                    Force=True, VolumeId=volume_id)
    except Exception as e:
        print(str(e))


def get_volume(ec2_client, volume_id):
    try:
        response = ec2_client.describe_volumes(VolumeIds=[volume_id])
        return response.get("Volumes")
    except ec2_client.exceptions.ClientError:
        return None


def success_empty_response():
    return {
        "statusCode": 200,
        "body": json.dumps(obj={"Message": "No instances to process"}, default=str),
    }

# Debug locally
# lambda_handler(None, None)
