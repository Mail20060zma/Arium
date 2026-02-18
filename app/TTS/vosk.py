def recognize_vosk(self, audio_data, language='en'):
    from vosk import KaldiRecognizer, Model

    assert isinstance(audio_data, AudioData), "Data must be audio data"

    if not hasattr(self, 'vosk_model'):
        if not os.path.exists("model"):
            return "Please download the model from https://alphacephei.com/vosk/models and unpack as 'model' in the current folder."
            exit(1)
        self.vosk_model = Model("model")

    rec = KaldiRecognizer(self.vosk_model, 16000)

    rec.AcceptWaveform(audio_data.get_raw_data(convert_rate=16000, convert_width=2))
    finalRecognition = rec.FinalResult()

    return finalRecognition