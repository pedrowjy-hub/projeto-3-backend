import os
from functools import lru_cache
from flask import Flask
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
app =  Flask(__name__)


@lru_cache
def get_client():
    return MongoClient(
        os.environ["MONGO_URI"],
        serverSelectionTimeoutMS=5000,
    )

def get_db():
    return get_client()[os.getenv("MONGO_DB", "projeto3")]


@app.route("/exemplo", methods=["GET"])
def exemplo():
    return get_client().admin.command("ping")


if __name__ == '__main__':
    app.run(debug=True)
