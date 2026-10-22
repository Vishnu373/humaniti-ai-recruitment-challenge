1. Analyzing the data {
I've uploaded the transcripts of the Excellent classes. Here are the few observations:
1. the ASR accuracy, depends on the ASR WER and  transcription phase architecture. 
2. Some transcribed words came out with wrong  intention, what the user meant and how it is depicted in the transcription is like little is wrong.  In short, misleading.

About the class:-
interaction/small talk -> interactive session/game -> new lesson

my question:-
Did I miss anything, if so justify it with proofs from the transcript.
low verbosity
}

2. Idea/approach {
User uploads the video (<= 500 MB) or provides a google drive link
- For storing the video, use an object storage - Cloudfare R2
- After that extract the audio
- Pass the audio through an ASR model (utilize hugging face serverless inference) for transcribing. ASR model which supports english and malayalam.
- After that, pass the transcript + quality evaluation criteria (docs/class_quality_checklist.md) in chunks to an LLM.
	- Prompt cache the quality evaluation criteria, previous transcript chunk and system prompt
	- Get the results. Pass it through the scoring calculator (docs/scoring_logic.md).
- Edge case:
1. Flag any transcript sentence is misleading (example - what user intended could be different from how the transcript word is portrayed)
     - Solution:-
         - The specific timeline will be shared
             - Option 1 - Human in the loop. A human checks it and adds his/her comment
             - Option 2 - Let the AI evaluate this specific clip. The AI will add it's comment
        - Be it option 1 or 2, the AI will now recalculate the score on top of the existing feedback. We aren't going to a full revaluation.
2. Teacher and student camera ON (doesn't need to be checked throughout the class, just get 3 instances - one in the beginning after 10 minutes and then in the middle (to be calculated) and finally by the end)
    - Same as option 1 solution
      
I haven't decided on ASR and LLM model yet. 
If I missed anything, comment it.
}

3. Status tags instead of polling {
Time to update the architecture: Right now the frontend keeps on polling which leads cloudflare hitting limits. Instead use status_tags as trigger. UPLOADING (when the file is being uploaded) -> EXTRACTING (audio extraction) -> TRANSCRIBING (transcribe the audio) -> EVALUATING (LLMs is judging) -> SCORE_APPLYING (when the score calculator gets used) -> COMPLETE. This frontend doesn't need keep on asking instead the backend puts these tags and acts a trigger.

Clarifications:
- Old internal statuses (awaiting_upload, downloading, uploaded, audio_ready, transcribed, failed): replace with the 6 tags + FAILED.
- Push method: asked for the trade-off between a Gradio stream and a FastAPI SSE endpoint, then: "Go with the Gradio stream"
}

4. Producer-consumer slicing for STT {
Now next architecture update is slicing the uploaded audio. use ffmpeg to slice the audio with 10 minute length (9minutes 45 seconds + 15 seconds overlap) and right away pass the audio slice to sarvam stt for transcribing. Add log messages for each slice status. Keep them in queue between ffmpeg and sarvam. Consider this as a producer-consumer system; where the ffpmeg slices the audio, pushes the audio into queue, sarvam ai checks the queue if any available transcribes and the process continues.

Clarifications:
- Sarvam consumers: small pool of 3.
- Overlap duplicates: asked for an explanation ("I didn't understand"), then: "Sitck with A" (cut at the midpoint of the shared 15 s).
}

5. Slice the video directly instead of extracting the full audio first {
How about we upload the file as slices rather than slicing it afterwards. What will be trade-off

So, instead of converting audio first. You make ffpmeg cut the video into slices, then convert each slice into audio pass it to sarvam and so on.

Let me understand it clearly again: 1. You upload the video to R2. You don't extract audio upon upload. Instead, you slice the video which gives out 16khz wav audio file. First slice pushed to queue, the sarvam picks it up transcribes and continues. Is my understanding correct

If we are going to do step 2 why not fully avoid step 1

Understood. But won't the previous plan put more strain on the server?

Proceed with this plan

Decision:
- Keep the browser → R2 upload (survives Space restarts, needed later for the camera-frame check, avoids pushing 500 MB through the Space proxy).
- The server downloads the video once; ffmpeg cuts each 10-min slice (15 s overlap) straight from the video into 16 kHz mono WAV and queues it for the 3 Sarvam workers.
- No full-length WAV and no WAV upload/download round trip through R2.
}
