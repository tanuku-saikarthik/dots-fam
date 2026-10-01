import { useEffect, useRef, useState } from 'react';
import { Mic, MicOff, PhoneOff } from 'lucide-react';
import { api, type Dot } from '../api';
import { DotMark } from './DotMark';

interface VoiceStatus {
  available: boolean;
  reason?: string | null;
  stack?: string;
  ice_servers?: string[];
}

type Phase = 'starting' | 'live' | 'ended' | 'failed';

function level(analyser: AnalyserNode, buffer: Uint8Array<ArrayBuffer>) {
  analyser.getByteTimeDomainData(buffer);
  let peak = 0;
  for (const value of buffer) peak = Math.max(peak, Math.abs(value - 128));
  return peak / 128;
}

function waitForIce(pc: RTCPeerConnection) {
  return new Promise<void>((resolve) => {
    if (pc.iceGatheringState === 'complete') return resolve();
    const done = () => {
      if (pc.iceGatheringState === 'complete') {
        pc.removeEventListener('icegatheringstatechange', done);
        resolve();
      }
    };
    pc.addEventListener('icegatheringstatechange', done);
    setTimeout(resolve, 2500); // good enough: host and STUN candidates arrive quickly
  });
}

export function CallBar({
  dot,
  threadId,
  onThread,
  onEnd,
}: {
  dot: Dot;
  threadId?: string;
  onThread: (id: string) => void;
  onEnd: () => void;
}) {
  const [phase, setPhase] = useState<Phase>('starting');
  const [error, setError] = useState('');
  const [muted, setMuted] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [talking, setTalking] = useState<'you' | 'dot' | null>(null);
  const call = useRef<{ pc?: RTCPeerConnection; mic?: MediaStream; pcId?: string; audio?: HTMLAudioElement; ctx?: AudioContext }>({});
  const thread = useRef(threadId);

  useEffect(() => {
    let cancelled = false;
    const state = call.current;
    let timer = 0;
    let meter = 0;
    (async () => {
      try {
        const status = await api<VoiceStatus>('/voice');
        if (!status.available) throw new Error(status.reason ?? 'Voice calls are not available.');
        const mic = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
        if (cancelled) return mic.getTracks().forEach((t) => t.stop());
        state.mic = mic;
        const pc = new RTCPeerConnection({ iceServers: (status.ice_servers ?? []).map((urls) => ({ urls })) });
        state.pc = pc;
        mic.getTracks().forEach((track) => pc.addTrack(track, mic));
        pc.createDataChannel('chat');
        const audio = new Audio();
        audio.autoplay = true;
        state.audio = audio;
        const ctx = new AudioContext();
        state.ctx = ctx;
        const buffer = new Uint8Array(new ArrayBuffer(1024));
        const mine = ctx.createAnalyser();
        mine.fftSize = 1024;
        ctx.createMediaStreamSource(mic).connect(mine);
        let theirs: AnalyserNode | undefined;
        pc.ontrack = (event) => {
          const stream = event.streams[0] ?? new MediaStream([event.track]);
          audio.srcObject = stream;
          void audio.play().catch(() => undefined);
          theirs = ctx.createAnalyser();
          theirs.fftSize = 1024;
          ctx.createMediaStreamSource(stream).connect(theirs);
        };
        pc.onconnectionstatechange = () => {
          if (pc.connectionState === 'connected') setPhase('live');
          if (pc.connectionState === 'failed') {
            setError('The call dropped. Check your network and try again.');
            setPhase('failed');
          }
        };
        await pc.setLocalDescription(await pc.createOffer());
        await waitForIce(pc);
        const answer = await api<{ sdp: string; type: RTCSdpType; pc_id: string; thread_id: string }>(
          `/voice/${dot.id}/offer`,
          'POST',
          { sdp: pc.localDescription!.sdp, type: 'offer', thread_id: thread.current ?? null },
        );
        if (cancelled) return;
        state.pcId = answer.pc_id;
        await pc.setRemoteDescription({ type: answer.type, sdp: answer.sdp });
        if (answer.thread_id !== thread.current) {
          thread.current = answer.thread_id;
          onThread(answer.thread_id);
        }
        timer = window.setInterval(() => setSeconds((s) => s + 1), 1000);
        meter = window.setInterval(() => {
          const dotLevel = theirs ? level(theirs, buffer) : 0;
          const myLevel = level(mine, buffer);
          setTalking(dotLevel > 0.06 ? 'dot' : myLevel > 0.08 ? 'you' : null);
        }, 120);
      } catch (e) {
        if (cancelled) return;
        setError(
          e instanceof DOMException && e.name === 'NotAllowedError'
            ? 'Microphone access was blocked. Allow it in your browser to call.'
            : e instanceof Error
              ? e.message
              : 'Could not start the call.',
        );
        setPhase('failed');
      }
    })();
    return () => {
      cancelled = true;
      clearInterval(timer);
      clearInterval(meter);
      if (state.pcId) void api('/voice/hangup', 'POST', { pc_id: state.pcId }).catch(() => undefined);
      state.pc?.close();
      state.mic?.getTracks().forEach((t) => t.stop());
      if (state.audio) state.audio.srcObject = null;
      void state.ctx?.close().catch(() => undefined);
    };
    // The call is set up once per mount; the parent remounts it for a new call.
  }, []);

  const toggleMute = () => {
    const next = !muted;
    call.current.mic?.getAudioTracks().forEach((t) => (t.enabled = !next));
    setMuted(next);
  };
  const clock = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;

  return (
    <div className={`call-bar ${phase}`} role="region" aria-label={`Call with ${dot.name}`}>
      <span className={`call-dot ${talking === 'dot' ? 'speaking' : ''}`}>
        <DotMark dot={dot} />
      </span>
      <div className="grow">
        <strong>
          {phase === 'starting' ? `Calling ${dot.name}…` : phase === 'live' ? `On a call with ${dot.name}` : 'Call ended'}
        </strong>
        <p className="muted small" aria-live="polite">
          {error ? (
            error
          ) : phase === 'live' ? (
            <>
              <span className="call-clock">{clock}</span>
              {muted
                ? 'You are muted'
                : talking === 'dot'
                  ? `${dot.name} is speaking`
                  : talking === 'you'
                    ? 'Listening…'
                    : 'Talk any time. The conversation shows up in this chat.'}
            </>
          ) : (
            'Connecting your microphone…'
          )}
        </p>
      </div>
      {phase === 'live' && (
        <button className="button" aria-pressed={muted} onClick={toggleMute}>
          {muted ? <MicOff size={16} /> : <Mic size={16} />} {muted ? 'Unmute' : 'Mute'}
        </button>
      )}
      <button className="button hangup" onClick={onEnd}>
        <PhoneOff size={16} /> {phase === 'failed' ? 'Close' : 'Hang up'}
      </button>
    </div>
  );
}
