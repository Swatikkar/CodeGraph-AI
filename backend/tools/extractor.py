import os
import shutil
import zipfile
from fastapi import UploadFile


UPLOAD_DIR = os.path.join(os.getcwd(),"uploads")

def extraction(file : UploadFile):
    if not os.path.exists(UPLOAD_DIR):
        os.makedirs(UPLOAD_DIR)
    
    folder_name = file.filename.replace(".zip","")
    folder_path = os.path.join(UPLOAD_DIR,folder_name)

    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)

    os.makedirs(folder_path)

    with zipfile.ZipFile(file.file, 'r') as zip_ref:
        zip_ref.extractall(folder_path)

    return {
        "status": "success",
        "folder_name": folder_name,
        "local_path": folder_path
    }