# Todo:
# we are not making them a vpc, they have to provide it.  regular vpc.  Regular internet gateways.
# create eb
# create sec groups for data processing servers
# create rabbitmq server
# create data processing servers
from pprint import pprint
from time import sleep

from deployment_helpers.aws.boto_helpers import (create_iam_client,
    create_eb_client)
from deployment_helpers.aws.elastic_beanstalk_configuration import (get_base_eb_configuration,
    DynamicParameter)
from deployment_helpers.aws.iam import (iam_find_role, IamEntityMissingError, iam_create_role,
    iam_attach_role_policy, iam_find_instance_profile, PythonPlatformDiscoveryError,
    EnvironmentDeploymentFailure)
from deployment_helpers.aws.rds import (add_eb_environment_to_rds_database_security_group)
from deployment_helpers.aws.security_groups import get_security_group_by_name
from deployment_helpers.constants import (OUR_CUSTOM_POLICY_ARN, EB_SERVICE_ROLE,
    EB_INSTANCE_PROFILE_ROLE, BEIWE_APPLICATION_NAME, EB_INSTANCE_PROFILE_NAME,
    EB_SEC_GRP_COUNT_ERROR, get_elasticbeanstalk_assume_role_policy_document,
    get_instance_assume_role_policy_document, AWS_EB_SERVICE, AWS_EB_ENHANCED_HEALTH,
    AWS_EB_MULTICONTAINER_DOCKER, AWS_EB_WEB_TIER, AWS_EB_WORKER_TIER, get_global_config,
    get_server_configuration_file,
    get_finalized_environment_variables)
from deployment_helpers.general_utils import retry, log, current_time_string


def construct_eb_environment_variables(eb_environment_name):
    global_config = get_global_config()
    try:
        environment_variables = get_finalized_environment_variables(eb_environment_name)
    except Exception as e:
        log.error("could not get your environment settings.")
        log.error(e)
        raise
    
    try:
        server_settings = get_server_configuration_file(eb_environment_name)
    except Exception as e:
        log.error("could not get your server settings.")
        log.error(e)
        raise
    # This needs to be a comma separated list of environment variables declared as "var=value"
    env_var_string = ",".join(["%s=%s" % (k, v) for k, v in environment_variables.iteritems()])
    
    generated_configuration_details = {
        "ServiceRole": get_or_create_eb_service_role()['Arn'],
        "IamInstanceProfile": get_or_create_eb_instance_profile()['Arn'],
        "EnvironmentVariables": env_var_string,
        "EC2KeyName": global_config["DEPLOYMENT_KEY_NAME"],
        "InstanceType": server_settings['ELASTIC_BEANSTALK_INSTANCE_TYPE'],
        "Notification Endpoint": global_config['SYSTEM_ADMINISTRATOR_EMAIL']
    }
    
    configuration = get_base_eb_configuration()
    for option in configuration:
        if isinstance(option['Value'], DynamicParameter):
            option['Value'] = generated_configuration_details.pop(option['OptionName'])
    
    if generated_configuration_details:
        pprint(generated_configuration_details)
        raise Exception("encountered unused autogenerated configs, see print statement above to debug.")
    
    return configuration


##
## AWS Accessors
##


def get_python27_platform_arn():
    """ gets the most recent platform arm for a python 2.7 elastic beanstalk cluster. """
    eb_client = create_eb_client()
    platforms = []
    for platform in eb_client.list_platform_versions()['PlatformSummaryList']:
        if (platform.get('PlatformCategory', None) == 'Python' and
                    "2.7" in platform.get('PlatformArn', [])):
            platforms.append(platform['PlatformArn'])
    
    if len(platforms) == 0:
        raise PythonPlatformDiscoveryError("could not find python 2.7 platform")
    if len(platforms) > 1:
        raise PythonPlatformDiscoveryError("encountered multiple python 2.7 platforms: %s" % platforms)
    if len(platforms) == 1:
        return platforms[0]


# ancient and terrible, probably good base to start from if we ever want a cli list of environments.
def get_environments_list():
    environments = create_eb_client().describe_environments()['Environments']
    return [environment['EnvironmentName'] for environment in environments]

##
## Creation Functions
##


def get_or_create_eb_service_role():
    """ This function creates the appropriate roles that apply to the elastic beanstalk environment,
    based of of the roles created when using the online AWS console. """
    iam_client = create_iam_client()
    
    try:
        iam_find_role(iam_client, EB_SERVICE_ROLE)
    except IamEntityMissingError:
        log.info("eb service role not found, creating...")
        iam_create_role(iam_client, EB_SERVICE_ROLE, get_elasticbeanstalk_assume_role_policy_document())
    
    iam_attach_role_policy(iam_client, EB_SERVICE_ROLE, AWS_EB_SERVICE)
    iam_attach_role_policy(iam_client, EB_SERVICE_ROLE, AWS_EB_ENHANCED_HEALTH)
    return iam_find_role(iam_client, EB_SERVICE_ROLE)


def get_or_create_eb_instance_profile_role():
    """ This function creates the appropriate roles that apply to the instances in an elastic
    beanstalk environment, based of of the roles created when using the online AWS console. """
    iam_client = create_iam_client()
    try:
        iam_find_role(iam_client, EB_INSTANCE_PROFILE_ROLE)
    except IamEntityMissingError:
        log.info("eb instance profile _role_ not found, creating...")
        iam_create_role(iam_client, EB_INSTANCE_PROFILE_ROLE, get_instance_assume_role_policy_document())
    # This first one is in the original role, but it is almost definitely not required.
    iam_attach_role_policy(iam_client, EB_INSTANCE_PROFILE_ROLE, AWS_EB_MULTICONTAINER_DOCKER)
    iam_attach_role_policy(iam_client, EB_INSTANCE_PROFILE_ROLE, AWS_EB_WEB_TIER)
    iam_attach_role_policy(iam_client, EB_INSTANCE_PROFILE_ROLE, AWS_EB_WORKER_TIER)
    return iam_find_role(iam_client, EB_INSTANCE_PROFILE_ROLE)


def get_or_create_eb_instance_profile():
    #     """ This function creates the appropriate roles that apply to the instances in an elastic
    #     beanstalk environment, based of of the roles created when using the online AWS console. """
    iam_client = create_iam_client()
    try:
        return iam_find_instance_profile(iam_client, EB_INSTANCE_PROFILE_NAME)
    except IamEntityMissingError:
        log.info("eb instance _profile_ not found, creating...")
        iam_client.create_instance_profile(
            InstanceProfileName=EB_INSTANCE_PROFILE_NAME)
        _ = iam_client.add_role_to_instance_profile(
                InstanceProfileName=EB_INSTANCE_PROFILE_NAME,
                RoleName=get_or_create_eb_instance_profile_role()['RoleName']
        )
    return iam_find_instance_profile(iam_client, EB_INSTANCE_PROFILE_NAME)


def get_or_create_eb_application():
    """
    https://docs.aws.amazon.com/elasticbeanstalk/latest/api/API_CreateApplication.html
    """
    eb_client = create_eb_client()
    
    applications = eb_client.describe_applications().get('Applications', None)
    for app in applications:
        app_name = app.get('ApplicationName', None)
        if app_name and BEIWE_APPLICATION_NAME in app_name.lower():
            log.info('Using Elastic Beanstalk application named "%s."' % app_name)
            return app_name
        
    # raise Exception("no beiwe applications found")
    return eb_client.create_application(
            ApplicationName=BEIWE_APPLICATION_NAME,
            Description='Your Beiwe Application',
            ResourceLifecycleConfig={
                'ServiceRole': OUR_CUSTOM_POLICY_ARN,
                # The ARN of an IAM service role that Elastic Beanstalk has permission to assume
                'VersionLifecycleConfig': {
                    'MaxCountRule': {
                        'Enabled': False,
                        'MaxCount': 1000,  # should be ignored
                        'DeleteSourceFromS3': True
                    },
                    'MaxAgeRule': {
                        'Enabled': False,
                        'MaxAgeInDays': 1000,  # should be ignored
                        'DeleteSourceFromS3': True
                    }
                }
            }
    )


def get_environment(eb_environment_name):
    eb_client = create_eb_client()
    return eb_client.describe_configuration_settings(
            ApplicationName="beiwe-application",
            EnvironmentName=eb_environment_name
            )['ConfigurationSettings'][0]


def get_eb_instance_security_group_identifier(eb_environment_name):
    for o in get_environment(eb_environment_name)['OptionSettings']:
        if (o['OptionName'] == 'SecurityGroups' and
            o['Namespace'] == 'aws:autoscaling:launchconfiguration' and
            o['ResourceName'] == 'AWSEBAutoScalingLaunchConfiguration'):
            grps = o['Value'].split(",")
            if len(grps) > 1:
                raise Exception(EB_SEC_GRP_COUNT_ERROR % eb_environment_name)
            return o['Value']


def get_eb_load_balancer_security_group_identifier(eb_environment_name):
    for o in get_environment(eb_environment_name)['OptionSettings']:
        if (o['OptionName'] == 'SecurityGroups' and
            o['Namespace'] == 'aws:elb:loadbalancer' and
            o['ResourceName'] == 'AWSEBLoadBalancer'):
            grps = o['Value'].split(",")
            if len(grps) > 1:
                raise Exception(EB_SEC_GRP_COUNT_ERROR % eb_environment_name)
            return o['Value']
        

def allow_eb_environment_database_access(eb_environment_name):
    """ This requires that the database be up and running with its own security groups finalized. """
    eb_sec_grp_name = get_eb_instance_security_group_identifier(eb_environment_name)
    eb_sec_grp_id = get_security_group_by_name(eb_sec_grp_name)['GroupId']
    add_eb_environment_to_rds_database_security_group(eb_environment_name, eb_sec_grp_id)
    
    
def create_eb_environment(eb_environment_name, without_db=False):
    # Don't actually use the without_db flag in production, it is for debugging
    app = get_or_create_eb_application()
    
    # if not without_db:
    #     try:
    #         _ = get_db_info(eb_environment_name)
    #     except DBInstanceNotFound:
    #         log.error("could not find a database named '%s,' you must create a database first."
    #                   % construct_db_name(eb_environment_name))
    #         EXIT()
    
    option_settings = construct_eb_environment_variables(eb_environment_name)
    
    log.info("creating a new Elastic Beanstalk environment named %s... this will take a while." % eb_environment_name)
    eb_client = create_eb_client()
    env = eb_client.create_environment(
            ApplicationName=BEIWE_APPLICATION_NAME,
            EnvironmentName=eb_environment_name,
            Description='elastic beanstalk beiwe cluster',
            PlatformArn=get_python27_platform_arn(),
            OptionSettings=option_settings,
            # VersionLabel='string',  # TODO: this will probably be required later?
        
            # a different form of configuration management
            # OptionsToRemove=[
            #     {'ResourceName': 'string',
            #      'Namespace': 'string',
            #      'OptionName': 'string'}]
            
            # Tags=[{'Key': 'string',
            #        'Value': 'string'}],
            
            # CNAMEPrefix='string',  # not required
            # Tier={'Name': 'string',
            #       'Type': 'string',
            #       'Version': 'string'},
            
            # GroupName='string',  # for use in other methods of eb configuration
            # TemplateName='string',  # nope
            # SolutionStackName='string', # more about templates
    )
    
    env_id = env['EnvironmentId']
    good_eb_environment_states = ["Launching", "Updating"]
    bad_eb_environment_states = ["Terminating", "Terminated"]
    
    while True:
        envs = retry(eb_client.describe_environments, EnvironmentIds=[env_id])['Environments']
        log.info('%s: Elastic Beanstalk status is "%s", waiting until status is "Ready"'
                 % (current_time_string(),  env['Status']))
        if len(envs) != 1:
            raise Exception("describe_environments is broken, %s environments returned" % len(envs))
        env = envs[0]
        if env['Status'] in bad_eb_environment_states:
            msg = "environment deployment failed:\n%s" % format(env)  #pprint format
            log.error(msg)  #python logging is weird and this fails to print if python exits too quickly.
            raise EnvironmentDeploymentFailure(msg)
        if env['Status'] in good_eb_environment_states:
            sleep(5)
            continue
        if env['Status'] == "Ready":
            log.info("environment %s, is ready to have Beiwe deployed to it." % eb_environment_name)
            break
            
    allow_eb_environment_database_access(eb_environment_name)
    return env


def check_if_eb_environment_exists(name):
    extant_environments = get_environments_list()
    if name.lower() in [n.lower() for n in extant_environments]:
        return True
    return False