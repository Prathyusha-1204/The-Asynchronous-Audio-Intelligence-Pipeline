from flask import Flask, render_template, request, jsonify
import boto3
import os
import time
import json
import uuid
import urllib.request
from dotenv import load_dotenv

# Load environment variables from the .env file in the same directory
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))

app = Flask(__name__)

# ==========================================
# CONFIGURATION - MUST BE UPDATED BY USER
# ==========================================
# These will now try to read from your .env file first
INPUT_BUCKET_NAME = os.getenv('S3_INPUT_BUCKET', 'audio-pipeline-input-bucket-123')
OUTPUT_BUCKET_NAME = os.getenv('S3_OUTPUT_BUCKET', 'audio-pipeline-output-bucket-123')
REGION_NAME = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
# ==========================================

# Initialize an S3 client using boto3 with explicit credentials
s3_client = boto3.client(
    's3', 
    region_name=REGION_NAME,
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID', '').strip(),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY', '').strip()
)

transcribe_client = boto3.client(
    'transcribe',
    region_name=REGION_NAME,
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID', '').strip(),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY', '').strip()
)


def transcribe_audio_from_s3(bucket_name, object_key, language_code='en-US', timeout_sec=900):
    """Start a Transcribe job and wait for completion (synchronous helper)."""
    job_name = f"transcribe-job-{uuid.uuid4().hex[:10]}"
    media_format = os.path.splitext(object_key)[1].lstrip('.').lower()

    media_uri = f"s3://{bucket_name}/{object_key}"

    transcribe_client.start_transcription_job(
        TranscriptionJobName=job_name,
        Media={'MediaFileUri': media_uri},
        MediaFormat=media_format,
        LanguageCode=language_code,
        OutputBucketName=OUTPUT_BUCKET_NAME,
        OutputKey=f"{os.path.splitext(object_key)[0]}.json"
    )

    start_time = time.time()
    while True:
        status_response = transcribe_client.get_transcription_job(TranscriptionJobName=job_name)
        job = status_response['TranscriptionJob']
        status = job['TranscriptionJobStatus']

        if status in ['COMPLETED', 'FAILED']:
            break

        if time.time() - start_time > timeout_sec:
            raise TimeoutError('Transcribe job timed out')

        time.sleep(5)

    if status == 'FAILED':
        raise RuntimeError(f"Transcription job failed: {job.get('FailureReason')}")

    # Download transcript JSON from the generated S3 JSON in output bucket as it can be immediate
    transcript_key = f"{os.path.splitext(object_key)[0]}.json"
    transcript_obj = s3_client.get_object(Bucket=OUTPUT_BUCKET_NAME, Key=transcript_key)
    transcript_json = json.loads(transcript_obj['Body'].read().decode('utf-8'))

    # Build plain text transcript for output .txt
    transcript_text = ''
    if transcript_json.get('results', {}).get('transcripts'):
        transcript_text = transcript_json['results']['transcripts'][0].get('transcript', '')

    # Save .txt file to output bucket
    text_key = f"{os.path.splitext(object_key)[0]}.txt"
    s3_client.put_object(Bucket=OUTPUT_BUCKET_NAME, Key=text_key, Body=transcript_text.encode('utf-8'))

    return transcript_json, transcript_text


@app.route('/')
def index():
    """Renders the main frontend interface."""
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    """Handles file uploads from the frontend and uploads to S3."""
    if 'audio_file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['audio_file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file:
        filename = file.filename
        # Record the exact second of upload to filter out old results
        upload_time = time.time()
        
        try:
            # Upload the file directly to the S3 Input Bucket
            s3_client.upload_fileobj(
                file, 
                INPUT_BUCKET_NAME, 
                filename,
                ExtraArgs={'ContentType': file.content_type}
            )

            # Optional: trigger transcription immediately by starting Transcribe job
            transcript_json, transcript_text = transcribe_audio_from_s3(INPUT_BUCKET_NAME, filename)

            # response includes both buckets/results for the frontend
            return jsonify({
                'message': f'{filename} successfully uploaded and transcribed.',
                'filename': filename,
                'upload_time': upload_time,
                'transcript': transcript_text,
                'transcript_json': transcript_json,
                'summary': transcript_text
            }), 200
        except Exception as e:
            print(f"Error uploading to S3: {e}")
            return jsonify({'error': str(e)}), 500

@app.route('/check_status', methods=['GET'])
def check_status():
    """
    Checks the S3 output bucket for both the summary and the full transcript.
    Only considers files created AFTER the provided upload_time.
    """
    filename = request.args.get('filename')
    upload_time = request.args.get('upload_time', type=float)
    
    if not filename or not upload_time:
        return jsonify({'error': 'Filename and upload_time are required'}), 400

    base_name = os.path.splitext(filename)[0].lower()
    
    from datetime import datetime, timezone, timedelta
    # Convert incoming timestamp to UTC datetime
    # We subtract 60 seconds as a "buffer" in case your computer's clock 
    # is slightly ahead of the AWS clock (clock skew).
    upload_dt = datetime.fromtimestamp(upload_time, tz=timezone.utc) - timedelta(seconds=60)
    
    print(f"\n--- POLLING S3 (Looking for files newer than: {upload_dt}) ---")

    summary_text = None
    transcript_text = None

    try:
        results = s3_client.list_objects_v2(Bucket=OUTPUT_BUCKET_NAME)
        
        if 'Contents' in results:
            # Sort newest first
            sorted_contents = sorted(results['Contents'], key=lambda x: x['LastModified'], reverse=True)
            
            for obj in sorted_contents:
                file_key = obj['Key']
                file_time = obj['LastModified']
                
                # IMPORTANT: Filter out old files
                if file_time < upload_dt:
                    # Since we sorted by date, all remaining files are even older
                    break

                print(f" NEW FILE DETECTED: {file_key} (Modified: {file_time})")
                
                # Check for Summary (.txt)
                if file_key.lower().endswith('.txt'):
                    print(f" -> FOUND SUMMARY: {file_key}")
                    response = s3_client.get_object(Bucket=OUTPUT_BUCKET_NAME, Key=file_key)
                    summary_text = response['Body'].read().decode('utf-8')
                
                # Check for Transcript (.json)
                if file_key.lower().endswith('.json'):
                    print(f" -> FOUND TRANSCRIPT: {file_key}")
                    response = s3_client.get_object(Bucket=OUTPUT_BUCKET_NAME, Key=file_key)
                    import json
                    transcript_data = json.loads(response['Body'].read().decode('utf-8'))
                    if 'results' in transcript_data and 'transcripts' in transcript_data['results']:
                        transcript_text = transcript_data['results']['transcripts'][0]['transcript']
            
            if not summary_text and not transcript_text:
                print(" No NEW results found in this poll.")
            
        if summary_text and transcript_text:
            return jsonify({
                'status': 'completed', 
                'summary': summary_text,
                'transcript': transcript_text
            }), 200
        elif transcript_text:
            return jsonify({
                'status': 'processing',
                'transcript_ready': True,
                'transcript': transcript_text
            }), 200
        else:
            return jsonify({'status': 'processing'}), 200

    except Exception as e:
        print(f"Error checking S3 status: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Run the Flask app in debug mode so it auto-reloads on changes
    app.run(debug=True, port=5000)
