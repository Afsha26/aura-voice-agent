import { useEffect, useRef, useState } from 'react';
import './App.css';
import { AudioPlaybackQueue } from './audioPlaybackQueue.js';

const OUTPUT_SAMPLE_RATE = 24_000;
const VOICE_DIAGNOSTICS_ENABLED = new URLSearchParams(window.location.search).has('voiceDebug');

function App() {
  const [callStatus, setCallStatus] = useState('idle');
  const [microphoneActive, setMicrophoneActive] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const [transcript, setTranscript] = useState([]);
  const [summary, setSummary] = useState(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState('');
  const [sampleOrders, setSampleOrders] = useState([]);
  const [ordersError, setOrdersError] = useState('');
  const socketRef = useRef(null);
  const audioContextRef = useRef(null);
  const playbackQueueRef = useRef(null);
  if (playbackQueueRef.current === null) {
    playbackQueueRef.current = new AudioPlaybackQueue();
  }
  const microphoneStreamRef = useRef(null);
  const microphoneNodeRef = useRef(null);
  const microphoneSourceRef = useRef(null);
  const silentGainRef = useRef(null);
  const silenceTimerRef = useRef(null);
  const connectionTimeoutRef = useRef(null);
  const callEndedRef = useRef(false);
  const pendingEndRef = useRef(false);
  const endCommandSentRef = useRef(false);
  const assistantTurnActiveRef = useRef(false);
  const serverEndedRef = useRef(false);
  const connectedRef = useRef(false);
  const callStatusRef = useRef('idle');
  const callIdRef = useRef(null);
  const summaryRequestedRef = useRef(null);
  const transcriptRef = useRef([]);
  const transcriptRenderFrameRef = useRef(null);
  const connectionResolveRef = useRef(null);
  const connectionRejectRef = useRef(null);
  const callStartedAtRef = useRef(0);
  const lastSpeechAtRef = useRef(0);
  const speechSilenceAtRef = useRef(0);
  const speechActiveRef = useRef(false);
  const firstAudioChunkRef = useRef(false);

  function logVoiceTiming(event, details = {}) {
    if (!VOICE_DIAGNOSTICS_ENABLED) return;
    const elapsed = callStartedAtRef.current
      ? Math.round(performance.now() - callStartedAtRef.current)
      : 0;
    console.debug(`[Aura voice +${elapsed}ms] ${event}`, details);
  }

  function updateStatus(status) {
    callStatusRef.current = status;
    setCallStatus(status);
  }

  function stopMicrophoneCapture() {
    window.clearTimeout(silenceTimerRef.current);
    silenceTimerRef.current = null;
    window.clearTimeout(connectionTimeoutRef.current);
    connectionTimeoutRef.current = null;

    if (microphoneNodeRef.current) {
      microphoneNodeRef.current.port.onmessage = null;
      microphoneNodeRef.current.disconnect();
      microphoneNodeRef.current = null;
    }
    microphoneSourceRef.current?.disconnect();
    microphoneSourceRef.current = null;
    silentGainRef.current?.disconnect();
    silentGainRef.current = null;

    microphoneStreamRef.current?.getTracks().forEach((track) => track.stop());
    microphoneStreamRef.current = null;
    setMicrophoneActive(false);
  }

  function releaseAudioPlayback() {
    const audioContext = audioContextRef.current;
    playbackQueueRef.current.clear(audioContext?.currentTime ?? 0);
    audioContextRef.current = null;
    if (audioContext && audioContext.state !== 'closed') {
      void audioContext.close();
    }
  }

  function releaseLocalResources() {
    stopMicrophoneCapture();
    releaseAudioPlayback();
  }

  function closeCallSocket() {
    const socket = socketRef.current;
    socketRef.current = null;
    if (socket && socket.readyState < WebSocket.CLOSING) {
      socket.close(1000, 'Call ended');
    }
  }

  function completeEndedCall() {
    if (!serverEndedRef.current || playbackQueueRef.current.sources.size > 0) return;
    releaseAudioPlayback();
    closeCallSocket();
    if (callStatusRef.current !== 'idle') updateStatus('idle');
    void requestSummary(callIdRef.current);
  }

  function maybeSendEndCommand() {
    if (
      !pendingEndRef.current
      || endCommandSentRef.current
      || assistantTurnActiveRef.current
      || playbackQueueRef.current.sources.size > 0
    ) {
      return;
    }
    pendingEndRef.current = false;
    endCommandSentRef.current = true;
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'end' }));
    } else {
      closeCallSocket();
    }
  }

  function releaseResources() {
    releaseLocalResources();
    closeCallSocket();
  }

  async function requestSummary(callId) {
    if (!callId || summaryRequestedRef.current === callId) return;
    summaryRequestedRef.current = callId;
    setSummaryLoading(true);
    setSummaryError('');
    try {
      const response = await fetch(`/calls/${encodeURIComponent(callId)}/summary`, {
        method: 'POST',
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || 'Could not generate the post-call summary.');
      }
      setSummary(result);
    } catch (error) {
      setSummaryError(error.message || 'Could not generate the post-call summary.');
    } finally {
      setSummaryLoading(false);
    }
  }

  function endCall() {
    if (callEndedRef.current) return;
    callEndedRef.current = true;
    pendingEndRef.current = true;
    connectionRejectRef.current?.(new Error('Call ended.'));
    connectionResolveRef.current = null;
    connectionRejectRef.current = null;
    stopMicrophoneCapture();
    connectedRef.current = false;
    setErrorMessage('');
    updateStatus('idle');
    if (callIdRef.current) {
      setSummary(null);
      setSummaryError('');
      setSummaryLoading(true);
    }
    maybeSendEndCommand();
  }

  function failCall(message) {
    if (callEndedRef.current) return;
    callEndedRef.current = true;
    connectionRejectRef.current?.(new Error(message));
    connectionResolveRef.current = null;
    connectionRejectRef.current = null;
    releaseResources();
    connectedRef.current = false;
    setErrorMessage(message);
    updateStatus('error');
  }

  function updateTranscriptEntry(message) {
    if (!message.turn_id || !message.text) return;
    const current = transcriptRef.current;
    const existingIndex = current.findIndex((entry) => entry.turn_id === message.turn_id);
    const entry = {
      turn_id: message.turn_id,
      role: message.role,
      text: message.text,
      timestamp: message.timestamp,
      complete: Boolean(message.complete),
    };
    const updated = existingIndex < 0
      ? [...current, entry]
      : current.map((item, index) => (index === existingIndex ? entry : item));
    transcriptRef.current = updated;
    if (entry.complete) {
      if (transcriptRenderFrameRef.current !== null) {
        window.cancelAnimationFrame(transcriptRenderFrameRef.current);
        transcriptRenderFrameRef.current = null;
      }
      setTranscript(updated);
    } else if (transcriptRenderFrameRef.current === null) {
      transcriptRenderFrameRef.current = window.requestAnimationFrame(() => {
        transcriptRenderFrameRef.current = null;
        setTranscript(transcriptRef.current);
      });
    }
  }

  function playAudio(audioData) {
    const audioContext = audioContextRef.current;
    if (!audioContext || audioData.byteLength === 0) return;
    if (audioData.byteLength % 2 !== 0) {
      failCall('Received an invalid audio packet from Gemini Live.');
      return;
    }

    const pcm = new Int16Array(audioData);
    const audioBuffer = audioContext.createBuffer(1, pcm.length, OUTPUT_SAMPLE_RATE);
    const channel = audioBuffer.getChannelData(0);
    for (let index = 0; index < pcm.length; index += 1) {
      channel[index] = pcm[index] / 32768;
    }

    if (!firstAudioChunkRef.current) {
      firstAudioChunkRef.current = true;
      logVoiceTiming('first audio chunk received', {
        sinceUserSpeechSilenceMs: speechSilenceAtRef.current
          ? Math.round(performance.now() - speechSilenceAtRef.current)
          : null,
      });
    }
    const arrivalAt = audioContext.currentTime;
    const queuedEndAt = playbackQueueRef.current.nextPlaybackTime;
    const startAt = playbackQueueRef.current.schedule(
      audioContext,
      audioBuffer,
      (remainingSources) => {
        logVoiceTiming('audio chunk finished', { remainingSources });
        if (remainingSources === 0 && connectedRef.current) {
          if (callStatusRef.current !== 'listening') updateStatus('listening');
        }
        maybeSendEndCommand();
        completeEndedCall();
      },
    );
    logVoiceTiming('audio chunk scheduled', {
      queuedSources: playbackQueueRef.current.sources.size,
      startDelayMs: Math.round(Math.max(0, startAt - arrivalAt) * 1000),
      playbackGapMs: Math.round(Math.max(0, arrivalAt - queuedEndAt) * 1000),
      durationMs: Math.round(audioBuffer.duration * 1000),
    });
    if (startAt <= arrivalAt + 0.01) {
      logVoiceTiming('audio chunk scheduled for immediate playback');
    }
    if (callStatusRef.current !== 'speaking') updateStatus('speaking');
  }

  function handleSocketMessage(event) {
    if (event.data instanceof ArrayBuffer) {
      playAudio(event.data);
      return;
    }

    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      failCall('Received an invalid message from the voice service.');
      return;
    }

    if (message.type === 'error') {
      const messageText = message.message || 'The voice service reported an error.';
      failCall(messageText);
      return;
    }
    if (callEndedRef.current && message.type === 'status') {
      if (message.interrupted) {
        playbackQueueRef.current.clear(audioContextRef.current?.currentTime ?? 0);
      }
      if (message.status === 'listening' && message.turn_complete) {
        assistantTurnActiveRef.current = false;
        maybeSendEndCommand();
      }
      return;
    }
    if (message.type === 'status' && message.status === 'connected') {
      window.clearTimeout(connectionTimeoutRef.current);
      connectionTimeoutRef.current = null;
      callIdRef.current = message.call_id;
      connectedRef.current = true;
      setErrorMessage('');
      updateStatus('listening');
      connectionResolveRef.current?.();
      connectionResolveRef.current = null;
      connectionRejectRef.current = null;
      return;
    }
    if (message.type === 'status' && message.status === 'speaking') {
      if (!assistantTurnActiveRef.current) {
        logVoiceTiming('first assistant response event', {
          sinceUserSpeechSilenceMs: speechSilenceAtRef.current
            ? Math.round(performance.now() - speechSilenceAtRef.current)
            : null,
        });
        firstAudioChunkRef.current = false;
      }
      assistantTurnActiveRef.current = true;
      if (callStatusRef.current !== 'speaking') updateStatus('speaking');
    } else if (message.type === 'status' && message.status === 'listening') {
      if (message.interrupted) {
        logVoiceTiming('assistant response interrupted; clearing queued audio');
        playbackQueueRef.current.clear(audioContextRef.current?.currentTime ?? 0);
        if (callStatusRef.current !== 'listening') updateStatus('listening');
      } else if (message.turn_complete) {
        logVoiceTiming('assistant response completed');
      }
      if (message.turn_complete) assistantTurnActiveRef.current = false;
      if (
        playbackQueueRef.current.sources.size === 0
        && callStatusRef.current !== 'listening'
      ) {
        updateStatus('listening');
      }
      maybeSendEndCommand();
    } else if (message.type === 'transcript') {
      updateTranscriptEntry(message);
    } else if (message.type === 'ended') {
      serverEndedRef.current = true;
      connectedRef.current = false;
      if (message.call_id) callIdRef.current = message.call_id;
      completeEndedCall();
    }
  }

  async function startMicrophoneAudio(audioContext, stream, socket) {
    await audioContext.audioWorklet.addModule('/mic-processor.js');
    const microphoneSource = audioContext.createMediaStreamSource(stream);
    const microphoneNode = new AudioWorkletNode(audioContext, 'aura-microphone-processor');
    const silentGain = audioContext.createGain();
    silentGain.gain.value = 0;

    microphoneNode.port.onmessage = (event) => {
      if (socket.readyState !== WebSocket.OPEN || callEndedRef.current) return;
      socket.send(event.data.pcm);

      if (event.data.speech) {
        if (!speechActiveRef.current) {
          speechActiveRef.current = true;
          logVoiceTiming('user speech detected');
        }
        lastSpeechAtRef.current = performance.now();
        if (callStatusRef.current !== 'speaking') updateStatus('thinking');
        window.clearTimeout(silenceTimerRef.current);
        silenceTimerRef.current = null;
      } else {
        if (speechActiveRef.current) {
          speechActiveRef.current = false;
          speechSilenceAtRef.current = performance.now();
          logVoiceTiming('user speech capture became silent', {
            sinceLastSpeechMs: Math.round(performance.now() - lastSpeechAtRef.current),
          });
        }
        if (callStatusRef.current === 'thinking' && silenceTimerRef.current === null) {
          silenceTimerRef.current = window.setTimeout(() => {
            silenceTimerRef.current = null;
            if (connectedRef.current && playbackQueueRef.current.sources.size === 0) {
              updateStatus('listening');
            }
          }, 900);
        }
      }
    };

    microphoneSource.connect(microphoneNode);
    microphoneNode.connect(silentGain);
    silentGain.connect(audioContext.destination);
    microphoneSourceRef.current = microphoneSource;
    microphoneNodeRef.current = microphoneNode;
    silentGainRef.current = silentGain;
    setMicrophoneActive(true);
  }

  async function startCall() {
    playbackQueueRef.current.clear(0);
    callStartedAtRef.current = performance.now();
    lastSpeechAtRef.current = 0;
    speechSilenceAtRef.current = 0;
    speechActiveRef.current = false;
    firstAudioChunkRef.current = false;
    callEndedRef.current = false;
    pendingEndRef.current = false;
    endCommandSentRef.current = false;
    assistantTurnActiveRef.current = false;
    serverEndedRef.current = false;
    connectedRef.current = false;
    callIdRef.current = null;
    summaryRequestedRef.current = null;
    setErrorMessage('');
    setSummary(null);
    setSummaryError('');
    setSummaryLoading(false);
    transcriptRef.current = [];
    setTranscript([]);
    if (transcriptRenderFrameRef.current !== null) {
      window.cancelAnimationFrame(transcriptRenderFrameRef.current);
      transcriptRenderFrameRef.current = null;
    }
    updateStatus('connecting');

    if (
      !navigator.mediaDevices?.getUserMedia
      || !window.AudioContext
      || !window.AudioWorkletNode
      || !window.WebSocket
    ) {
      failCall('This browser does not support the audio features required for a voice call.');
      return;
    }
    if (!window.isSecureContext) {
      failCall('Microphone access requires HTTPS or a secure localhost connection.');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      if (callEndedRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      microphoneStreamRef.current = stream;

      const audioContext = new AudioContext();
      audioContextRef.current = audioContext;
      await audioContext.resume();

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const socket = new WebSocket(`${protocol}//${window.location.host}/ws/voice`);
      socket.binaryType = 'arraybuffer';
      socketRef.current = socket;
      socket.onmessage = handleSocketMessage;
      socket.onclose = () => {
        if (!callEndedRef.current) {
          failCall('The voice connection closed. Please start a new call.');
        }
      };
      socket.onerror = () => {
        if (!callEndedRef.current) {
          failCall('Could not connect to the voice service. Check that the backend is running.');
        }
      };

      const connected = new Promise((resolve, reject) => {
        connectionResolveRef.current = resolve;
        connectionRejectRef.current = reject;
        connectionTimeoutRef.current = window.setTimeout(() => {
          if (!connectedRef.current && !callEndedRef.current) {
            reject(new Error('Timed out while connecting to Gemini Live.'));
          }
        }, 30_000);
      });
      await connected;
      await startMicrophoneAudio(audioContext, stream, socket);
    } catch (error) {
      if (callEndedRef.current) return;
      if (error.name === 'NotAllowedError' || error.name === 'SecurityError') {
        failCall('Microphone permission was denied. Allow microphone access and try again.');
      } else if (error.name === 'NotFoundError' || error.name === 'DevicesNotFoundError') {
        failCall('No microphone was found. Connect a microphone and try again.');
      } else {
        failCall(error.message || 'Could not start the voice call.');
      }
    }
  }

  useEffect(() => {
    let active = true;
    fetch('/sample-orders')
      .then(async (response) => {
        if (!response.ok) throw new Error('Could not load the sample orders.');
        return response.json();
      })
      .then((orders) => {
        if (active) setSampleOrders(orders);
      })
      .catch((error) => {
        if (active) setOrdersError(error.message || 'Could not load the sample orders.');
      });
    return () => {
      active = false;
      callEndedRef.current = true;
      releaseResources();
      if (transcriptRenderFrameRef.current !== null) {
        window.cancelAnimationFrame(transcriptRenderFrameRef.current);
      }
    };
  }, []);

  const statusLabels = {
    idle: 'Ready',
    connecting: 'Connecting',
    listening: 'Listening',
    thinking: 'Thinking',
    speaking: 'Speaking',
    error: 'Error',
  };
  const callInProgress = ['connecting', 'listening', 'thinking', 'speaking'].includes(callStatus);

  return (
    <main className="app-shell">
      <header className="page-header">
        <p className="eyebrow">AURA SKINCARE</p>
        <h1>Aura Skincare Support</h1>
        <p className="subtitle">Realtime help with orders, delivery, and skincare policies.</p>
      </header>

      <section className="assistant-intro" aria-label="Aria introduction">
        <div className="assistant-intro-copy">
          <span className="assistant-avatar" aria-hidden="true">A</span>
          <div>
            <h2>Aria <span>· Aura support assistant</span></h2>
            <p>Ask about an order, delivery status, returns, or payment policies.</p>
          </div>
        </div>
        <div className="example-questions" aria-label="Example questions">
          <span>“Where is order 101?”</span>
          <span>“What is your return policy?”</span>
          <span>“Can I cancel order 103?”</span>
        </div>
      </section>

      <div className="content-grid">
        <section className="panel transcript-panel" aria-labelledby="transcript-heading">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">CALL NOTES</p>
              <h2 id="transcript-heading">Conversation</h2>
            </div>
            <span className="panel-label">{transcript.length} turns</span>
          </div>
          <div className="transcript-list" aria-live="polite" aria-relevant="additions text">
            {transcript.length === 0 ? (
              <div className="transcript-empty">
                <span className="transcript-icon" aria-hidden="true">Aa</span>
                <p>Your conversation will appear here.</p>
                <span className="muted-text">Start a call and say hello to Aria.</span>
              </div>
            ) : (
              transcript.map((entry, index) => (
                <article className={`transcript-entry ${entry.role}`} key={entry.turn_id || `${index}-${entry.role}`}>
                  <strong>{entry.role === 'user' ? 'You' : 'Aria'}</strong>
                  <p>{entry.text}</p>
                  {entry.timestamp && (
                    <time dateTime={entry.timestamp}>
                      {new Date(entry.timestamp).toLocaleTimeString()}
                    </time>
                  )}
                </article>
              ))
            )}
          </div>
          {errorMessage && (
            <p className="error-message" role="alert">
              {errorMessage}
            </p>
          )}
        </section>

        <section className="panel orders-panel" aria-labelledby="orders-heading">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">CUSTOMER CARE</p>
              <h2 id="orders-heading">Sample orders</h2>
            </div>
            <span className="panel-label">{sampleOrders.length} orders</span>
          </div>
          {ordersError ? (
            <p className="error-message" role="alert">{ordersError}</p>
          ) : (
            <div className="orders-table-wrap">
              <table className="orders-table">
                <thead>
                  <tr>
                    <th scope="col">Order ID</th>
                    <th scope="col">Product</th>
                    <th scope="col">Total</th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {sampleOrders.map((order) => (
                    <tr key={order.order_id}>
                      <td>{order.order_id}</td>
                      <td>{order.product}</td>
                      <td>₹{order.total_inr}</td>
                      <td>{order.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {sampleOrders.length === 0 && (
                <p className="muted-text">Loading sample orders…</p>
              )}
            </div>
          )}
        </section>
      </div>
      <section className="panel summary-panel" aria-labelledby="summary-heading">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">AFTER THE CALL</p>
            <h2 id="summary-heading">Call summary</h2>
          </div>
          <span className="panel-label">Structured JSON</span>
        </div>
        {summaryLoading ? (
          <p className="summary-message" role="status">Generating the post-call summary…</p>
        ) : summaryError ? (
          <>
            <p className="error-message" role="alert">
              {summaryError} Your transcript remains available above.
            </p>
            {callIdRef.current && (
              <button
                className="button button-secondary"
                type="button"
                onClick={() => {
                  summaryRequestedRef.current = null;
                  void requestSummary(callIdRef.current);
                }}
              >
                Retry summary
              </button>
            )}
          </>
        ) : summary ? (
          <pre className="summary-json">{JSON.stringify(summary, null, 2)}</pre>
        ) : (
          <p className="summary-message muted-text">
            End a call to generate its structured summary.
          </p>
        )}
      </section>
      <footer className="call-control-bar" aria-label="Voice call controls">
        <div className="call-control-inner">
          <div className="call-control-status">
            <span className={`status-indicator ${callStatus}`} aria-hidden="true" />
            <span>{statusLabels[callStatus]}</span>
            <span className={`microphone-state ${microphoneActive ? 'active' : ''}`}>
              {microphoneActive ? 'Mic on' : 'Mic off'}
            </span>
          </div>
          <div className="call-control-actions">
            <button
              className="button button-primary"
              type="button"
              onClick={startCall}
              disabled={callInProgress || summaryLoading}
            >
              <svg aria-hidden="true" viewBox="0 0 20 20" className="microphone-icon">
                <path d="M10 12.5a3 3 0 0 0 3-3v-4a3 3 0 1 0-6 0v4a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H3a7 7 0 0 0 6 6.93V19h2v-2.57A7 7 0 0 0 17 9.5h-2Z" />
              </svg>
              Start Call
            </button>
            <button
              className="button button-secondary"
              type="button"
              onClick={endCall}
              disabled={!callInProgress}
            >
              End Call
            </button>
          </div>
        </div>
      </footer>
    </main>
  );
}

export default App;
