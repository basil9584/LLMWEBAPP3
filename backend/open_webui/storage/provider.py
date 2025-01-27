import logging
import os
from typing import BinaryIO, Tuple

import boto3
from botocore.exceptions import ClientError
from open_webui.config import (
    S3_ACCESS_KEY_ID,
    S3_BUCKET_NAME,
    S3_ENDPOINT_URL,
    S3_REGION_NAME,
    S3_SECRET_ACCESS_KEY,
    GCS_BUCKET_NAME,
    GOOGLE_APPLICATION_CREDENTIALS_JSON,
    STORAGE_PROVIDER,
    UPLOAD_DIR,
)

# Add B2 configuration to config.py
from open_webui.config import (
    B2_BUCKET_NAME,
    B2_ENDPOINT_URL,
    B2_KEY_ID,
    B2_KEY_SECRET,
)

from google.cloud import storage
from google.cloud.exceptions import GoogleCloudError, NotFound
from open_webui.constants import ERROR_MESSAGES

from b2sdk.v2 import InMemoryAccountInfo, B2Api

class StorageProvider(ABC):
    @abstractmethod
    def get_file(self, file_path: str) -> str:
        pass

    @abstractmethod
    def upload_file(self, file: BinaryIO, filename: str) -> Tuple[bytes, str]:
        pass

    @abstractmethod
    def delete_all_files(self) -> None:
        pass

    @abstractmethod
    def delete_file(self, file_path: str) -> None:
        pass

class LocalStorageProvider(StorageProvider):
    @staticmethod
    def upload_file(file: BinaryIO, filename: str) -> Tuple[bytes, str]:
        contents = file.read()
        if not contents:
            raise ValueError(ERROR_MESSAGES.EMPTY_CONTENT)
        file_path = f"{UPLOAD_DIR}/{filename}"
        with open(file_path, "wb") as f:
            f.write(contents)
        return contents, file_path

    @staticmethod
    def get_file(file_path: str) -> str:
        """Handles downloading of the file from local storage."""
        return file_path

    @staticmethod
    def delete_file(file_path: str) -> None:
        """Handles deletion of the file from local storage."""
        filename = file_path.split("/")[-1]
        file_path = f"{UPLOAD_DIR}/{filename}"
        if os.path.isfile(file_path):
            os.remove(file_path)
        else:
            print(f"File {file_path} not found in local storage.")

    @staticmethod
    def delete_all_files() -> None:
        """Handles deletion of all files from local storage."""
        if os.path.exists(UPLOAD_DIR):
            for filename in os.listdir(UPLOAD_DIR):
                file_path = os.path.join(UPLOAD_DIR, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)  # Remove the file or link
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)  # Remove the directory
                except Exception as e:
                    print(f"Failed to delete {file_path}. Reason: {e}")
        else:
            print(f"Directory {UPLOAD_DIR} not found in local storage.")

class S3StorageProvider(StorageProvider):
    def __init__(self):
        self.s3_client = boto3.client(
            "s3",
            region_name=S3_REGION_NAME,
            endpoint_url=S3_ENDPOINT_URL,
            aws_access_key_id=S3_ACCESS_KEY_ID,
            aws_secret_access_key=S3_SECRET_ACCESS_KEY,
        )
        self.bucket_name = S3_BUCKET_NAME

    def upload_file(self, file: BinaryIO, filename: str) -> Tuple[bytes, str]:
        """Handles uploading of the file to S3 storage."""
        _, file_path = LocalStorageProvider.upload_file(file, filename)
        try:
            self.s3_client.upload_file(file_path, self.bucket_name, filename)
            return (
                open(file_path, "rb").read(),
                "s3://" + self.bucket_name + "/" + filename,
            )
        except ClientError as e:
            raise RuntimeError(f"Error uploading file to S3: {e}")

    def get_file(self, file_path: str) -> str:
        """Handles downloading of the file from S3 storage."""
        try:
            bucket_name, key = file_path.split("//")[1].split("/")
            local_file_path = f"{UPLOAD_DIR}/{key}"
            self.s3_client.download_file(bucket_name, key, local_file_path)
            return local_file_path
        except ClientError as e:
            raise RuntimeError(f"Error downloading file from S3: {e}")

    def delete_file(self, file_path: str) -> None:
        """Handles deletion of the file from S3 storage."""
        filename = file_path.split("/")[-1]
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=filename)
        except ClientError as e:
            raise RuntimeError(f"Error deleting file from S3: {e}")

        # Always delete from local storage
        LocalStorageProvider.delete_file(file_path)

    def delete_all_files(self) -> None:
        """Handles deletion of all files from S3 storage."""
        try:
            response = self.s3_client.list_objects_v2(Bucket=self.bucket_name)
            if "Contents" in response:
                for content in response["Contents"]:
                    self.s3_client.delete_object(
                        Bucket=self.bucket_name, Key=content["Key"]
                    )
        except ClientError as e:
            raise RuntimeError(f"Error deleting all files from S3: {e}")

        # Always delete from local storage
        LocalStorageProvider.delete_all_files()

class GCSStorageProvider(StorageProvider):
    def __init__(self):
        self.bucket_name = GCS_BUCKET_NAME

        if GOOGLE_APPLICATION_CREDENTIALS_JSON:
            self.gcs_client = storage.Client.from_service_account_info(
                info=json.loads(GOOGLE_APPLICATION_CREDENTIALS_JSON)
            )
        else:
            # if no credentials json is provided, credentials will be picked up from the environment
            # if running on local environment, credentials would be user credentials
            # if running on a Compute Engine instance, credentials would be from Google Metadata server
            self.gcs_client = storage.Client()
        self.bucket = self.gcs_client.bucket(GCS_BUCKET_NAME)

    def upload_file(self, file: BinaryIO, filename: str) -> Tuple[bytes, str]:
        """Handles uploading of the file to GCS storage."""
        contents, file_path = LocalStorageProvider.upload_file(file, filename)
        try:
            blob = self.bucket.blob(filename)
            blob.upload_from_filename(file_path)
            return contents, "gs://" + self.bucket_name + "/" + filename
        except GoogleCloudError as e:
            raise RuntimeError(f"Error uploading file to GCS: {e}")

    def get_file(self, file_path: str) -> str:
        """Handles downloading of the file from GCS storage."""
        try:
            filename = file_path.removeprefix("gs://").split("/")[1]
            local_file_path = f"{UPLOAD_DIR}/{filename}"
            blob = self.bucket.get_blob(filename)
            blob.download_to_filename(local_file_path)

            return local_file_path
        except NotFound as e:
            raise RuntimeError(f"Error downloading file from GCS: {e}")

    def delete_file(self, file_path: str) -> None:
        """Handles deletion of the file from GCS storage."""
        try:
            filename = file_path.removeprefix("gs://").split("/")[1]
            blob = self.bucket.get_blob(filename)
            blob.delete()
        except NotFound as e:
            raise RuntimeError(f"Error deleting file from GCS: {e}")

        # Always delete from local storage
        LocalStorageProvider.delete_file(file_path)

    def delete_all_files(self) -> None:
        """Handles deletion of all files from GCS storage."""
        try:
            blobs = self.bucket.list_blobs()

            for blob in blobs:
                blob.delete()

        except NotFound as e:
            raise RuntimeError(f"Error deleting all files from GCS: {e}")

        # Always delete from local storage
        LocalStorageProvider.delete_all_files()

class B2StorageProvider(StorageProvider):
    def __init__(self):
        info = InMemoryAccountInfo()
        self.b2_api = B2Api(info)
        self.b2_api.authorize_account("production", B2_KEY_ID, B2_KEY_SECRET)
        self.bucket_name = B2_BUCKET_NAME
        self.bucket = self.b2_api.get_bucket_by_name(self.bucket_name)

    def upload_file(self, file: BinaryIO, filename: str) -> Tuple[bytes, str]:
        """Handles uploading of the file to B2 storage."""
        contents, file_path = LocalStorageProvider.upload_file(file, filename)
        try:
            # Upload the file
            b2_file = self.bucket.upload_local_file(
                local_file=file_path,
                file_name=filename,
            )

            # Construct the download URL
            file_id = b2_file.id_
            download_url = f"{B2_ENDPOINT_URL}/file/{self.bucket_name}/{filename}?fileId={file_id}"

            return contents, download_url
        except Exception as e:
            raise RuntimeError(f"Error uploading file to B2: {e}")

    def get_file(self, file_path: str) -> str:
        """Handles downloading of the file from B2 storage."""
        try:
            # Extract the file name (key) from the B2 file path.
            file_name = file_path.split("/")[-1]

            # Download the file.
            file_content = self.bucket.download_file_by_name(file_name).get_bytes_written()

            # Save the downloaded file locally (you might want to customize the path).
            local_file_path = os.path.join(UPLOAD_DIR, file_name)
            with open(local_file_path, "wb") as local_file:
                local_file.write(file_content)

            return local_file_path
        except Exception as e:
            raise RuntimeError(f"Error downloading file from B2: {e}")

    def delete_file(self, file_path: str) -> None:
        """Handles deletion of the file from B2 storage."""
        try:
            file_name = file_path.split("/")[-1]
            file_version = self.bucket.get_file_info_by_name(file_name).as_dict()
            self.bucket.delete_file_version(file_version["fileId"], file_name)
        except Exception as e:
            raise RuntimeError(f"Error deleting file from B2: {e}")

        # Always delete from local storage
        LocalStorageProvider.delete_file(file_path)

    def delete_all_files(self) -> None:
        """Handles deletion of all files from B2 storage."""
        try:
            for file_version in self.bucket.ls():
                self.bucket.delete_file_version(
                    file_version.id_, file_version.file_name
                )
        except Exception as e:
            raise RuntimeError(f"Error deleting all files from B2: {e}")

        # Always delete from local storage
        LocalStorageProvider.delete_all_files()

def get_storage_provider(storage_provider: str):
    if storage_provider == "local":
        Storage = LocalStorageProvider()
    elif storage_provider == "s3":
        Storage = S3StorageProvider()
    elif storage_provider == "gcs":
        Storage = GCSStorageProvider()
    elif storage_provider == "b2":
        Storage = B2StorageProvider()
    else:
        raise RuntimeError(f"Unsupported storage provider: {storage_provider}")
    return Storage

Storage = get_storage_provider(STORAGE_PROVIDER)
