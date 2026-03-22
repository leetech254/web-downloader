import os
import tempfile
import uuid
from flask import Flask, request, render_template, send_file
import yt_dlp

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download():
    url = request.form.get('url')
    format_type = request.form.get('format')
    quality = request.form.get('quality')
    
    if not url:
        return "Error: Please provide a valid URL.", 400

    # Create a safe, temporary space for the download
    temp_dir = tempfile.mkdtemp()
    unique_filename = str(uuid.uuid4())
    
    # ULTIMATE BASE CONFIGURATION (Bypasses bots and region locks)
    ydl_opts = {
        'outtmpl': f'{temp_dir}/{unique_filename}.%(ext)s',
        'quiet': True,
        'noplaylist': True, 
        'nocheckcertificate': True,
        'geo_bypass': True, 
        'http_headers': {   
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }
    }

    # Failsafe: Only use cookies if the file is actually in the folder
    if os.path.exists('cookies.txt'):
        ydl_opts['cookiefile'] = 'cookies.txt'

    # FORMAT & QUALITY LOGIC
    if format_type == 'mp3':
        audio_quality = quality if quality in ['320', '192', '128'] else '192'
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [
                {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': audio_quality,
                }
            ],
        })
    else:
        # Bulletproof video formatting for ANY social media site
        if quality == 'best':
            fmt_string = 'bestvideo+bestaudio/best'
        elif quality in ['1080', '720', '480']:
            # Tries to find exact size, if not, falls back to the best available under that size
            fmt_string = f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best'
        else:
            fmt_string = 'bestvideo[height<=1080]+bestaudio/best[height<=1080]/best'

        ydl_opts.update({
            'format': fmt_string,
            'merge_output_format': 'mp4',
        })

    # EXECUTE DOWNLOAD
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Lee_Tech_Media')
            
            # Locate the final file
            downloaded_file = None
            for file in os.listdir(temp_dir):
                if file.startswith(unique_filename):
                    downloaded_file = os.path.join(temp_dir, file)
                    break
            
            if downloaded_file:
                # Clean the title so browsers don't block the download
                clean_title = "".join([c for c in title if c.isalnum() or c in " -_"]).strip()
                extension = downloaded_file.split('.')[-1]
                return send_file(downloaded_file, as_attachment=True, download_name=f"{clean_title}.{extension}")
            else:
                return "Error: Could not process the file. Please try a different link.", 500

    except Exception as e:
        return f"An error occurred: {str(e)}", 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)