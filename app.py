from flask import Flask, request, render_template, send_file
import yt_dlp
import os
import tempfile
import uuid

app = Flask(__name__)

# Route for the home page
@app.route('/')
def index():
    return render_template('index.html')

# Route that handles the download button click
@app.route('/download', methods=['POST'])
def download():
    url = request.form.get('url')
    format_type = request.form.get('format')
    
    if not url:
        return "Error: Please provide a valid URL.", 400

    # Create a temporary folder on the server to hold the file briefly
    temp_dir = tempfile.mkdtemp()
    unique_filename = str(uuid.uuid4()) # Creates a random file name to prevent mix-ups
    
    ydl_opts = {
        'outtmpl': f'{temp_dir}/{unique_filename}.%(ext)s',
        'quiet': True,
    }

    if format_type == 'mp3':
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        ydl_opts.update({
            'format': 'bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Download the file to the server
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'downloaded_media')
            
            # Locate the exact file that was just downloaded
            downloaded_file = None
            for file in os.listdir(temp_dir):
                if file.startswith(unique_filename):
                    downloaded_file = os.path.join(temp_dir, file)
                    break
            
            if downloaded_file:
                # Send the file from the server to the user's personal browser
                clean_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
                extension = downloaded_file.split('.')[-1]
                return send_file(downloaded_file, as_attachment=True, download_name=f"{clean_title}.{extension}")
            else:
                return "Error: Could not process the file.", 500

    except Exception as e:
        return f"An error occurred during download: {str(e)}", 500

# Start the web server
if __name__ == '__main__':
    # We use 0.0.0.0 and port 5000 so Render can hook into it
    app.run(host='0.0.0.0', port=5000)