from flask import Flask, request, render_template, send_file
import yt_dlp
import os
import tempfile
import uuid

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download():
    url = request.form.get('url')
    format_type = request.form.get('format')
    
    if not url:
        return "Error: Please provide a valid URL.", 400

    temp_dir = tempfile.mkdtemp()
    unique_filename = str(uuid.uuid4())
    
    # ULTIMATE CONFIGURATION: Works across all major social media
    ydl_opts = {
        'outtmpl': f'{temp_dir}/{unique_filename}.%(ext)s',
        'quiet': True,
        'noplaylist': True, # Prevents downloading entire channels by accident
        'nocheckcertificate': True,
        'geo_bypass': True, # Bypasses country locks
        'http_headers': {   # Fakes a real web browser to bypass TikTok/Insta bot blocks
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }
    }

    if format_type == 'mp3':
        ydl_opts.update({
            'format': 'bestaudio/best',
            'writethumbnail': True, # Grabs the cover art
            'postprocessors': [
                {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                },
                {
                    'key': 'EmbedThumbnail', # Embeds cover art into the MP3
                },
                {
                    'key': 'FFmpegMetadata', # Embeds title and artist info
                }
            ],
        })
    else:
        # Best video up to 1080p to prevent server timeout + best audio, combined to MP4
        ydl_opts.update({
            'format': 'bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'merge_output_format': 'mp4',
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract info and download
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Pro_Download')
            
            # Find the final processed file in the temp folder
            downloaded_file = None
            for file in os.listdir(temp_dir):
                if file.startswith(unique_filename) and not file.endswith('.webp') and not file.endswith('.jpg'):
                    downloaded_file = os.path.join(temp_dir, file)
                    break
            
            if downloaded_file:
                # Clean the title to prevent browser download errors
                clean_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
                extension = downloaded_file.split('.')[-1]
                return send_file(downloaded_file, as_attachment=True, download_name=f"{clean_title}.{extension}")
            else:
                return "Error: Could not process the file.", 500

    except Exception as e:
        return f"An error occurred: {str(e)}", 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)