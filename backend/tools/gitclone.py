import os 
import shutil
import git

UPLOAD_DIR = os.path.join(os.getcwd(),"uploads")
def clone(url:str):
    if not url.startswith("https://github.com/"):
        raise ValueError("invalid repo url")
    if not os.path.exists(UPLOAD_DIR):
        os.mkdir(UPLOAD_DIR)

    repo_name = url.split("/")[-1].split(".")[0]
    target = os.path.join(UPLOAD_DIR,repo_name)

    if os.path.exists(target):
        shutil.rmtree(target)
    git.Repo.clone_from(url,target)

    return {
        "status":"success",
        "repo_name0":repo_name,
        "target":target
    }
