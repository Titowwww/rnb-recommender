import os
import requests
import pandas as pd
import json
from flask import jsonify
from flask import Flask, redirect, request, session, url_for, render_template
from dotenv import load_dotenv
from urllib.parse import urlencode

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
USER_PROFILE_URL = "https://api.spotify.com/v1/me"

SCOPE = "user-read-private user-read-email playlist-modify-private playlist-modify-public"

CSV_PATH = 'data/rnb_with_spotify_links.csv'  # pastikan file CSV kamu ada di path ini

def get_artist_from_spotify(track_id, token):
    url = f"https://api.spotify.com/v1/tracks/{track_id}"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        data = r.json()
        artists = data.get("artists", [])
        if artists:
            return ", ".join(artist["name"] for artist in artists)
    return "Unknown"

@app.route("/")
def login():
    return render_template("login.html")

@app.route("/login")
def spotify_login():
    query_params = urlencode({
        "client_id": SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": SPOTIFY_REDIRECT_URI,
        "scope": SCOPE,
        "show_dialog": "true"
    })
    return redirect(f"{AUTH_URL}?{query_params}")

@app.route("/callback")
def callback():
    code = request.args.get("code")

    response = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": SPOTIFY_REDIRECT_URI,
        "client_id": SPOTIFY_CLIENT_ID,
        "client_secret": SPOTIFY_CLIENT_SECRET,
    })

    try:
        token_info = response.json()
        access_token = token_info.get("access_token")
        if not access_token:
            print("Access token kosong atau invalid:", token_info)
            return "Gagal mendapatkan access token dari Spotify."
    except Exception as e:
        print("Gagal decode token:", response.text)
        return "Gagal mendapatkan access token. Cek koneksi atau kredensial."

    # Ambil profil pengguna
    user_profile_response = requests.get(USER_PROFILE_URL, headers={
        "Authorization": f"Bearer {access_token}"
    })

    try:
        user_profile = user_profile_response.json()
    except Exception as e:
        print("Gagal parsing user profile:", user_profile_response.status_code, user_profile_response.text)
        return "Gagal mengambil profil pengguna dari Spotify."

    # Simpan ke session
    session["user"] = {
        "name": user_profile.get("display_name"),
        "email": user_profile.get("email"),
        "id": user_profile.get("id")
    }
    session["access_token"] = access_token

    return redirect(url_for("home"))

@app.route("/home")
def home():
    if "user" not in session:
        return redirect(url_for("login"))
    return render_template("home.html", user=session["user"])

@app.route("/mood_trend")
def mood_trend():
    df = pd.read_csv(CSV_PATH)

    # pastikan release date berupa tahun saja
    df['Year'] = pd.to_datetime(df['Release Date'], errors='coerce').dt.year

    grouped = df.groupby(['Year', 'Mood']).size().reset_index(name='Count')

    # Ubah format ke dict per mood → { "happy": [...], "sad": [...], ... }
    result = {}
    for mood in grouped['Mood'].unique():
        mood_data = grouped[grouped['Mood'] == mood][['Year', 'Count']].to_dict(orient='records')
        result[mood] = mood_data

    return jsonify(result)

@app.route("/show_tracks", methods=["GET", "POST"])
def show_tracks():
    if "user" not in session:
        return redirect(url_for("login"))

    mood = request.form.get("mood") or request.args.get("mood")
    track_count = int(request.form.get("trackCount") or request.args.get("trackCount") or 5)

    df = pd.read_csv(CSV_PATH)
    df_mood = df[df["Mood"].str.lower() == mood.lower()]

    sampled_tracks = df_mood.sample(n=min(track_count, len(df_mood))).to_dict(orient="records")
    access_token = session.get("access_token")
    playlist = []

    for track in sampled_tracks:
        try:
            spotify_link = track["Spotify_Link"]
            track_id = spotify_link.split("/")[-1].split("?")[0]
            artist_name = get_artist_from_spotify(track_id, access_token)
            track["Artist"] = artist_name
        except:
            track["Artist"] = "Unknown"
        playlist.append(track)

    session["playlist"] = playlist
    session["mood"] = mood
    session["trackCount"] = track_count

    return render_template("showed.html", playlist=playlist, mood=mood, trackCount=track_count)

@app.route("/refresh_playlist", methods=["POST"])
def refresh_playlist():
    mood = request.form.get("mood")
    track_count = request.form.get("trackCount")

    session["mood"]=mood
    session["trackcount"]=track_count
    return redirect(url_for("show_tracks", mood=mood, trackCount=track_count))

@app.route("/save_to_spotify", methods=["POST"])
def save_to_spotify():
    if "user" not in session or "access_token" not in session:
        session["trackCount"] = session.get("trackCount") or 5
        session["mood"] = session.get("mood")
        return redirect(url_for("login"))

    playlist_data = session.get("playlist")
    user_id = session["user"]["id"]
    access_token = session["access_token"]

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "name": f"R&BEATS - {session['mood']} Playlist",
        "description": "Generated by R&BEATS",
        "public": False
    }

    create = requests.post(
        f"https://api.spotify.com/v1/users/{user_id}/playlists",
        headers=headers,
        json=payload
    )

    if create.status_code != 201:
        print("Error:", create.status_code, create.text)
        return render_template("message.html", message="Gagal membuat playlist.")

    try:
        playlist_id = create.json().get("id")
    except:
        return render_template("message.html", message="Gagal membaca respons dari Spotify.")

    track_uris = []
    for song in playlist_data:
        try:
            link = song["Spotify_Link"]
            uri = link.split("/")[-1].split("?")[0]
            track_uris.append(f"spotify:track:{uri}")
        except:
            continue

    if playlist_id and track_uris:
        add_response = requests.post(
            f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks",
            headers=headers,
            json={"uris": track_uris}
        )

        if add_response.status_code == 201:
             return render_template(
            "message.html",
            message="Playlist berhasil disimpan ke akun Spotify!",
            mood=session['mood'],
            trackCount=session['trackCount']
            )
        else:
            return render_template("message.html", message="Gagal menambahkan lagu ke playlist.")
    else:
        return render_template("message.html", message="Gagal menyimpan playlist. Cek kembali datanya.")
    
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))  # langsung ke Spotify login

app = app
