from minio import Minio
from minio.error import S3Error
import os
import io
from .config import settings

class MinioClient:
    def __init__(self):
        self.endpoint = settings.MINIO_ENDPOINT
        self.access_key = settings.MINIO_ACCESS_KEY
        self.secret_key = settings.MINIO_SECRET_KEY
        self.secure = settings.MINIO_SECURE
        self.client = Minio(
            self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure
        )
        self.bucket_name = "documents"

    def _ensure_bucket_exists(self):
        try:
            if not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name)
        except S3Error as e:
            print(f"Error ensuring bucket exists: {e}")
            raise

    def upload_file(self, bucket_name: str, object_name: str, data: bytes, length: int, content_type: str):
        try:
            self.client.put_object(
                bucket_name=bucket_name,
                object_name=object_name,
                data=io.BytesIO(data),
                length=length,
                content_type=content_type
            )
        except S3Error as e:
            print(f"Error uploading file: {e}")
            raise

    def delete_file(self, bucket_name: str, object_name: str):
        try:
            self.client.remove_object(bucket_name, object_name)
        except S3Error as e:
            print(f"Error deleting file: {e}")
            raise

    def get_file(self, bucket_name: str, object_name: str):
        try:
            response = self.client.get_object(bucket_name, object_name)
            data = response.read()
            response.close()
            response.release_conn()
            return data
        except S3Error as e:
            print(f"Error getting file: {e}")
            raise
