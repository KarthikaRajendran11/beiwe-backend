from typing import Generator, List, Union
import boto3
from botocore.client import BaseClient
from Cryptodome.PublicKey import RSA

from config.settings import (BEIWE_SERVER_AWS_ACCESS_KEY_ID, BEIWE_SERVER_AWS_SECRET_ACCESS_KEY,
    S3_BUCKET, S3_REGION_NAME)
from database.study_models import Study
from database.user_models import Participant
from libs.aes import decrypt_server, encrypt_for_server
from libs.internal_types import StrOrParticipantOrStudy
from libs.rsa import generate_key_pairing, get_RSA_cipher, prepare_X509_key_for_java


"""
Research on getting a stream into the decryption code of pycryptodome

The StreamingBody StreamingBody object does not define the __len__ function, which is
necessary for creating a buffer somewhere in the decryption code, but it is possible to monkeypatch
it in like this:
    import botocore.response
    def monkeypatch_len(self):
        return int(self._content_length)
    botocore.response.StreamingBody.__len__ = monkeypatch_len

But that just results in this error from pycryptodome:
TypeError: Object type <class 'botocore.response.StreamingBody'> cannot be passed to C code
"""


class S3VersionException(Exception): pass
class NoSuchKeyException(Exception): pass


conn: BaseClient = boto3.client(
    's3',
    aws_access_key_id=BEIWE_SERVER_AWS_ACCESS_KEY_ID,
    aws_secret_access_key=BEIWE_SERVER_AWS_SECRET_ACCESS_KEY,
    region_name=S3_REGION_NAME,
)


# NOTE: the S3_BUCKET variable is patched during tests to be the Exception class
def smart_get_study_encryption_key(obj: StrOrParticipantOrStudy) -> bytes:
    if isinstance(obj, Participant):
        return obj.study.encryption_key.encode()
    elif isinstance(obj, Study):
        return obj.encryption_key.encode()
    elif isinstance(obj, str) and len(obj) == 24:
        return Study.objects.values_list("encryption_key", flat=True).get(object_id=obj).encode()
    else:
        raise TypeError(f"expected Study, Participant, or str, received '{type(obj)}'")


def s3_construct_study_key_path(key_path: str, obj: StrOrParticipantOrStudy):
    if isinstance(obj, Participant):
        study_object_id = obj.study.object_id
    elif isinstance(obj, Study):
        study_object_id = obj.study_object_id
    elif isinstance(obj, str) and len(obj) == 24:
        study_object_id = obj
    else:
        raise TypeError(f"expected Study, Participant, or 24 char str, received '{type(obj)}'")
    return study_object_id + "/" + key_path


# def s3_construct_participant_key_path(key_path: str, study_object_id: str, patient_id: str):
# return study_object_id + "/" + patient_id + "/" + key_path


def s3_upload(
    key_path: str, data_string: bytes, obj: StrOrParticipantOrStudy, raw_path=False
) -> None:
    """ uploads the provided file data to the provided S3 key path, failures raise exceptions. """
    if not raw_path:
        key_path = s3_construct_study_key_path(key_path, obj)
    data = encrypt_for_server(data_string, smart_get_study_encryption_key(obj))
    assert S3_BUCKET is not Exception, "libs.s3.s3_upload called inside test"
    conn.put_object(Body=data, Bucket=S3_BUCKET, Key=key_path)


def s3_retrieve(
    key_path: str, obj: StrOrParticipantOrStudy, raw_path:bool=False, number_retries=3
) -> bytes:
    """ Takes an S3 file path (key_path), and a study ID.  Takes an optional argument, raw_path,
    which defaults to false.  When set to false the path is prepended to place the file in the
    appropriate study_id folder. """
    if not raw_path:
        key_path = s3_construct_study_key_path(key_path, obj)
    encrypted_data = _do_retrieve(S3_BUCKET, key_path, number_retries=number_retries)['Body'].read()
    assert S3_BUCKET is not Exception, "libs.s3.s3_retrieve called inside test"
    return decrypt_server(encrypted_data, smart_get_study_encryption_key(obj))


def _do_retrieve(bucket_name, key_path, number_retries=3):
    """ Run-logic to do a data retrieval for a file in an S3 bucket."""
    assert S3_BUCKET is not Exception, "libs.s3._s3_retrieve(!!!) called inside test"
    try:
        return conn.get_object(Bucket=bucket_name, Key=key_path, ResponseContentType='string')
    
    except Exception as boto_error_unknowable_type:
        # Some error types cannot be imported because they are generated at runtime through a factory
        if boto_error_unknowable_type.__class__.__name__ == "NoSuchKey":
            raise NoSuchKeyException(f"{bucket_name}: {key_path}")
        # usually we want to try again
        if number_retries > 0:
            print("s3_retrieve failed, retrying on %s" % key_path)
            return _do_retrieve(bucket_name, key_path, number_retries=number_retries - 1)
        # unknown cases: explode.
        raise


def s3_list_files(prefix, as_generator=False) -> List[str]:
    """ Method fetches a list of filenames with prefix.
        note: entering the empty string into this search without later calling
        the object results in a truncated/paginated view."""
    assert S3_BUCKET is not Exception, "libs.s3.s3_list_files called inside test"
    return _do_list_files(S3_BUCKET, prefix, as_generator=as_generator)


def smart_s3_list_study_files(prefix: str, obj: StrOrParticipantOrStudy):
    assert S3_BUCKET is not Exception, "libs.s3.smart_s3_list_study_files called inside test"
    return s3_list_files(s3_construct_study_key_path(prefix, obj))


def s3_list_versions(prefix, allow_multiple_matches=False):
    """
    Page structure - each page is a dictionary with these keys:
     Name, ResponseMetadata, Versions, MaxKeys, Prefix, KeyMarker, IsTruncated, VersionIdMarker
    We only care about 'Versions', which is a list of all object versions matching that prefix.
    Versions is a list of dictionaries with these keys:
     LastModified, VersionId, ETag, StorageClass, Key, Owner, IsLatest, Size

    returns a list of dictionaries.
    If allow_multiple_matches is False the keys are LastModified, VersionId, IsLatest.
    If allow_multiple_matches is True the key 'Key' is added, containing the s3 file path.
    """
    
    paginator = conn.get_paginator('list_object_versions')
    assert S3_BUCKET is not Exception, "libs.s3.s3_list_versions called inside test"
    page_iterator = paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix)
    
    versions = []
    for page in page_iterator:
        # versions are not guaranteed, usually this means the file was deleted and only has deletion markers.
        if 'Versions' not in page:
            continue
        
        for s3_version in page['Versions']:
            if not allow_multiple_matches and s3_version['Key'] != prefix:
                raise S3VersionException("the prefix '%s' was not an exact match" % prefix)
            versions.append({
                'VersionId': s3_version["VersionId"],
                'Key': s3_version['Key'],
            })
    return versions


def _do_list_files(bucket_name, prefix, as_generator=False) -> List[str]:
    paginator = conn.get_paginator('list_objects_v2')
    assert S3_BUCKET is not Exception, "libs.s3.__s3_list_files(!!!) called inside test"
    page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=prefix)
    if as_generator:
        return _do_list_files_generator(page_iterator)
    else:
        items = []
        for page in page_iterator:
            if 'Contents' in page:
                for item in page['Contents']:
                    items.append(item['Key'].strip("/"))
        return items


def _do_list_files_generator(page_iterator):
    for page in page_iterator:
        if 'Contents' not in page:
            return
        for item in page['Contents']:
            yield item['Key'].strip("/")


def s3_delete(key_path):
    raise Exception("NO DONT DELETE")

################################################################################
######################### Client Key Management ################################
################################################################################


def create_client_key_pair(patient_id, study_id):
    """Generate key pairing, push to database, return sanitized key for client."""
    public, private = generate_key_pairing()
    s3_upload("keys/" + patient_id + "_private", private, study_id)
    s3_upload("keys/" + patient_id + "_public", public, study_id)


def get_client_public_key_string(patient_id, study_id) -> str:
    """Grabs a user's public key string from s3."""
    key_string = s3_retrieve("keys/" + patient_id + "_public", study_id)
    return prepare_X509_key_for_java(key_string).decode()


def get_client_public_key(patient_id, study_id) -> RSA.RsaKey:
    """Grabs a user's public key file from s3."""
    key = s3_retrieve("keys/" + patient_id +"_public", study_id)
    return get_RSA_cipher(key)


def get_client_private_key(patient_id, study_id) -> RSA.RsaKey:
    """Grabs a user's private key file from s3."""
    key = s3_retrieve("keys/" + patient_id +"_private", study_id)
    return get_RSA_cipher(key)
