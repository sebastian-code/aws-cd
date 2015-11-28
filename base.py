import time
import random
import string
import os
from dotenv import load_dotenv

import boto3
from botocore.exceptions import ClientError, DataNotFoundError


dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path)

SERVICE_ROLE_NAME = os.environ.get("SERVICE_ROLE_NAME")
INSTANCE_PROFILE_NAME = os.environ.get("INSTANCE_PROFILE_NAME")
APPLICATION_NAME = input("Application Name: ")
AMI_ID = input("AMI ID: ")

session = boto3.session.Session(profile_name=APPLICATION_NAME)


def get_random_password():
    password = ("".join([random.SystemRandom().choice(
                string.digits +
                string.ascii_letters) for i in range(16)]))
    return password


def create_service_role(session):
    iam = session.client('iam')
    response = iam.create_role(
        Path='/',
        RoleName=SERVICE_ROLE_NAME,
        AssumeRolePolicyDocument=open('codedeploy_trust.json').read(),
    )

    iam_resource = session.resource('iam')
    role = iam_resource.Role(name=SERVICE_ROLE_NAME)
    while True:
        try:
            response = role.attach_policy(
                PolicyArn='arn:aws:iam::aws:policy/service-role/AWSCodeDeployRole'
            )

        except(ClientError, DataNotFoundError):
            time.sleep(2)

        else:
            break

    return iam_resource.Role(name=SERVICE_ROLE_NAME)


def get_service_role(session):
    iam = session.resource('iam')
    role = iam.Role(SERVICE_ROLE_NAME)
    try:
        role.role_id

    except(ClientError, DataNotFoundError):
        role = create_service_role(session)


def create_instance_profile(session):
    iam = session.client('iam')
    response = iam.create_role(
        Path='/',
        RoleName=INSTANCE_PROFILE_NAME,
        AssumeRolePolicyDocument=open('codedeploy_ec2_trust.json').read(),
    )

    iam_resource = session.resource('iam')
    role = iam_resource.Role(name=INSTANCE_PROFILE_NAME)
    while True:
        try:
            response = iam.put_role_policy(
                RoleName=INSTANCE_PROFILE_NAME,
                PolicyName='codedeploy_ec2_permissions',
                PolicyDocument=open('codedeploy_ec2_permissions.json').read(),
            )

        except(ClientError, DataNotFoundError):
            time.sleep(2)

        else:
            break

    instance_profile = iam_resource.InstanceProfile(INSTANCE_PROFILE_NAME)
    try:
        instance_profile.arn

    except ClientError:
        response = iam.create_instance_profile(
                    InstanceProfileName=INSTANCE_PROFILE_NAME,
                    Path='/'
                )
        instance_profile = iam_resource.InstanceProfile(INSTANCE_PROFILE_NAME)

    while True:
        try:
            response = instance_profile.add_role(
                RoleName=INSTANCE_PROFILE_NAME,
            )

        except(ClientError, DataNotFoundError):
            time.sleep(2)
            
        else:
            break

    return instance_profile


def get_instance_profile(session):
    iam = session.resource('iam')
    role = iam.Role(INSTANCE_PROFILE_NAME)
    try:
        role.role_id

    except(ClientError, DataNotFoundError):
        role = create_instance_profile(session)

    return role


def create_codedeploy_app(session, service_role):
    DEPLOYMENT_GROUP_NAME = APPLICATION_NAME + '_deployment_group'
    codedeploy = session.client('codedeploy')
    response = codedeploy.create_application(
        applicationName=APPLICATION_NAME
    )
    response = codedeploy.create_deployment_group(
                applicationName=APPLICATION_NAME,
                deploymentGroupName=DEPLOYMENT_GROUP_NAME,
                deploymentConfigName='CodeDeployDefault.OneAtATime',
                ec2TagFilters=[
                        {
                            'Key': 'Name',
                            'Value': APPLICATION_NAME,
                            'Type': 'KEY_AND_VALUE'
                        },
                    ],
                serviceRoleArn=service_role.arn
                )
    print('CodeDeploy application: ' + APPLICATION_NAME)
    print('DeploymentGroup: ' + DEPLOYMENT_GROUP_NAME)
    return codedeploy.get_application(applicationName=APPLICATION_NAME)


def get_codedeploy_app(session):
    codedeploy = session.client('codedeploy')
    try:
        app = codedeploy.get_application(applicationName=APPLICATION_NAME)
    except ClientError:
        iam = session.resource('iam')
        service_role = iam.Role(SERVICE_ROLE_NAME)
        app = create_codedeploy_app(session, service_role)


def create_ec2_instance(session):
    ec2 = session.client('ec2')
    instance_profile = get_instance_profile(session)
    iam_resource = session.resource('iam')
    response = ec2.run_instances(
                ImageId=AMI_ID,
                MinCount=1,
                MaxCount=1,
                KeyName=APPLICATION_NAME,
                InstanceType='t2.micro',
                DisableApiTermination=False,
                InstanceInitiatedShutdownBehavior='stop',
                IamInstanceProfile={
                    'Arn': iam_resource.InstanceProfile(INSTANCE_PROFILE_NAME).arn,
                },
            )
    response = ec2.create_tags(
                Resources=[
                    response['Instances'][0]['InstanceId'],
                ],
                Tags=[
                    {
                        'Key': 'Name',
                        'Value': APPLICATION_NAME
                    },
                ]
            )


def get_ec2_instance(session):
    ec2 = session.resource('ec2')
    instances = ec2.instances.filter(Filters=[{'Name': 'tag:key', 'Values': [APPLICATION_NAME]}])
    if len(list(instances)) == 0:
        return create_ec2_instance(session)


def get_rds_instance(session):
    RDS_MASTER_USER = 'dbadmin'
    rds = session.client('rds')
    # TODO Check if RDS instance exists. Currently not supported
    # by boto3
    password = get_random_password()
    response = rds.create_db_instance(
                DBName=APPLICATION_NAME,
                DBInstanceIdentifier=APPLICATION_NAME + '-rds',
                AllocatedStorage=10,
                DBInstanceClass='db.t1.micro',
                Engine='postgres',
                MasterUsername=RDS_MASTER_USER,
                MasterUserPassword=password,
                MultiAZ=False,
                EngineVersion='9.4.1',
                AutoMinorVersionUpgrade=True,
                PubliclyAccessible=True,
                Tags=[
                    {
                        'Key': 'Name',
                        'Value': APPLICATION_NAME + '_db'
                    },
                ],
                StorageType='standard',
    )
    print('RDS database admin: ' + RDS_MASTER_USER)
    print('RDS database password: ' + password)


def create_s3_buckets(session):
    APP_BUCKET_NAME = APPLICATION_NAME + '-app'
    BUILDS_BUCKET_NAME = APPLICATION_NAME + '-builds'
    s3 = session.client('s3')
    s3_resource = session.resource('s3')
    app_buckets = s3_resource.buckets.all()
    app_buckets = [x.name for x in app_buckets]
    if APP_BUCKET_NAME not in app_buckets:
        response = s3.create_bucket(
            ACL='public-read',
            Bucket=APP_BUCKET_NAME,
        )
    if BUILDS_BUCKET_NAME not in app_buckets:
        response = s3.create_bucket(
            ACL='private',
            Bucket=BUILDS_BUCKET_NAME,
            CreateBucketConfiguration={
               'LocationConstraint': 'us-west-2',
            },
        )
    print('S3 Application bucket: ' + APP_BUCKET_NAME)
    print('S3 Builds bucket: ' + BUILDS_BUCKET_NAME)

service_role = get_service_role(session)
instance_profile = get_instance_profile(session)
codedeploy_app = get_codedeploy_app(session)
ec2_instance = get_ec2_instance(session)
rds_instance = get_rds_instance(session)
create_s3_buckets(session)
