import { useEffect, useRef, useState } from "react";
import {
  Box,
  Text,
  HStack,
  VStack,
  Button,
  Slider,
  Badge,
  Separator,
} from "@chakra-ui/react";
import { HiStop } from "react-icons/hi";
import { LuWaves, LuMonitor, LuChevronRight } from "react-icons/lu";
import {
  stopStream,
  testTone,
  startPassthrough,
  getAudioTips,
  getPassthroughConfig,
} from "../api";
import { loadUiPrefs, saveUiPrefs } from "../persistedPrefs";
import VuMeters from "./VuMeters";
import MultiTrackRecorder from "./MultiTrackRecorder";

const SAMPLE_RATES = [44100, 48000, 88200, 96000];
const USB_PCM_CHANNELS = 4;

const PASSTHROUGH_BODY_FALLBACK = {
  capture_backend: "portaudio",
  macos_output_volume: 100,
  routing_delay_sec: 0.03,
  capture_blocksize: 320,
  channels: USB_PCM_CHANNELS,
  sample_rate: 48000,
};

function Panel({ title, children }) {
  return (
    <Box
      bg="gray.900"
      p={4}
      borderRadius="xl"
      border="1px solid"
      borderColor="gray.800"
      boxShadow="sm"
    >
      {title ? (
        <Text
          fontSize="xs"
          color="gray.500"
          mb={3}
          fontWeight="medium"
          textTransform="uppercase"
          letterSpacing="0.06em"
        >
          {title}
        </Text>
      ) : null}
      {children}
    </Box>
  );
}

export default function StreamControls({ stats, onAction, wsSendVolume }) {
  const [boot] = useState(() => loadUiPrefs());
  const [volume, setVolume] = useState([boot.stream.volumePercent]);
  const [sampleRate, setSampleRate] = useState(48000);
  const [passthroughError, setPassthroughError] = useState(null);
  const [routing, setRouting] = useState(false);
  const volDebounceRef = useRef(null);
  const isStreaming = stats?.streaming;

  useEffect(() => () => clearTimeout(volDebounceRef.current), []);

  const [audioTips, setAudioTips] = useState(null);
  const [passthroughDefaults, setPassthroughDefaults] = useState(PASSTHROUGH_BODY_FALLBACK);

  useEffect(() => {
    getAudioTips()
      .then((d) => setAudioTips(Array.isArray(d?.tips) ? d.tips : null))
      .catch(() => setAudioTips(null));
  }, []);
  useEffect(() => {
    getPassthroughConfig()
      .then((d) => {
        if (d && typeof d.defaults === "object" && d.defaults !== null) {
          setPassthroughDefaults({ ...PASSTHROUGH_BODY_FALLBACK, ...d.defaults });
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const t = setTimeout(() => {
      saveUiPrefs({ stream: { volumePercent: volume[0] } });
    }, 200);
    return () => clearTimeout(t);
  }, [volume]);

  const pushVolumeWs = (pct) => {
    if (!wsSendVolume) return;
    if (volDebounceRef.current) clearTimeout(volDebounceRef.current);
    volDebounceRef.current = setTimeout(() => {
      wsSendVolume(Math.max(0, Math.min(1, pct / 100)));
    }, 50);
  };

  const isPassthrough = stats?.mode === "passthrough";
  const liveUsb =
    stats?.streaming && stats?.sample_rate != null
      ? `${(stats.sample_rate / 1000).toFixed(1)} kHz · 16 IN / 4 OUT`
      : null;

  const handleStop = async () => {
    await stopStream();
    onAction?.();
  };

  const handleTestTone = async () => {
    await testTone({
      volume: volume[0] / 100,
      sample_rate: sampleRate,
      channels: USB_PCM_CHANNELS,
    });
    onAction?.();
  };

  const handlePassthrough = async () => {
    if (isPassthrough) {
      setPassthroughError(null);
      setRouting(true);
      try {
        await stopStream();
      } finally {
        setRouting(false);
      }
    } else {
      setPassthroughError(null);
      setRouting(true);
      try {
        const lvl = Math.max(0, Math.min(1, volume[0] / 100));
        const r = await startPassthrough({
          ...passthroughDefaults,
          volume: lvl,
          sample_rate: sampleRate,
          channels: USB_PCM_CHANNELS,
        });
        if (r.error) {
          setPassthroughError(r.error);
          onAction?.();
          return;
        }
        wsSendVolume?.(lvl);
      } finally {
        setRouting(false);
      }
    }
    onAction?.();
  };

  return (
    <VStack gap={4} align="stretch">
      {/* Sample Rate Selector */}
      <Panel title="Sample Clock">
        <VStack gap={2} align="stretch">
          <HStack justify="space-between" align="center">
            <Text fontSize="sm" color="gray.400">
              Hardware Clock Rate
            </Text>
            {liveUsb && (
              <Badge colorPalette="teal" variant="subtle" size="sm">
                Active: {liveUsb}
              </Badge>
            )}
          </HStack>
          <HStack gap={2}>
            {SAMPLE_RATES.map((sr) => (
              <Button
                key={sr}
                flex={1}
                size="xs"
                variant={sampleRate === sr ? "solid" : "outline"}
                colorPalette={sampleRate === sr ? "teal" : "gray"}
                onClick={() => setSampleRate(sr)}
                disabled={isStreaming}
              >
                {(sr / 1000).toFixed(1)} kHz
              </Button>
            ))}
          </HStack>
        </VStack>
      </Panel>

      {/* Live VU Meters */}
      <VuMeters inPeak={stats?.in_peak} outPeak={stats?.out_peak} />

      {/* 16-Track Audio Recorder */}
      <MultiTrackRecorder stats={stats} sampleRate={sampleRate} onAction={onAction} />

      {/* Output & Playback Controls */}
      <Panel title="Output Volume">
        <VStack gap={4} align="stretch">
          <HStack justify="space-between" align="center" flexWrap="wrap" gap={2}>
            <Badge colorPalette="teal" variant="subtle" size="sm">
              USB 4ch Outputs (1-4)
            </Badge>
            {stats?.midi_rx != null && (
              <Badge colorPalette="purple" variant="subtle" size="sm">
                MIDI: {stats.midi_rx} RX / {stats.midi_tx} TX
              </Badge>
            )}
          </HStack>

          <Box>
            <HStack justify="space-between" mb={2}>
              <Text fontSize="sm" color="gray.400">
                Master Level
              </Text>
              <Text fontSize="sm" color="gray.200" fontWeight="semibold" tabularNums>
                {volume[0]}%
              </Text>
            </HStack>
            <Slider.Root
              value={volume}
              onValueChange={(e) => {
                setVolume(e.value);
                pushVolumeWs(e.value[0]);
              }}
              min={0}
              max={100}
              step={1}
            >
              <Slider.Control>
                <Slider.Track bg="gray.800">
                  <Slider.Range bg="teal.400" />
                </Slider.Track>
                <Slider.Thumb index={0} bg="white" boxSize={4} borderWidth={2} borderColor="teal.500" />
              </Slider.Control>
            </Slider.Root>
          </Box>
        </VStack>
      </Panel>

      {passthroughError ? (
        <Box
          bg="red.950"
          border="1px solid"
          borderColor="red.800"
          borderRadius="lg"
          px={3}
          py={2.5}
        >
          <Text fontSize="sm" color="red.200" fontWeight="medium">
            Couldn&apos;t start routing
          </Text>
          <Text fontSize="xs" color="red.300" mt={1} whiteSpace="pre-wrap">
            {passthroughError}
          </Text>
        </Box>
      ) : null}

      <Panel title="Actions">
        <VStack gap={3} align="stretch">
          <Button
            size="lg"
            colorPalette={isPassthrough ? "orange" : "blue"}
            onClick={handlePassthrough}
            loading={routing}
            disabled={routing}
          >
            <LuMonitor />
            {isPassthrough ? "Stop routing" : "Route macOS audio"}
          </Button>
          <HStack gap={2}>
            <Button
              flex={1}
              size="md"
              variant="outline"
              colorPalette="gray"
              onClick={handleTestTone}
              disabled={routing}
            >
              <LuWaves />
              Test tone
            </Button>
            {isStreaming ? (
              <Button
                flex={1}
                size="md"
                variant="outline"
                colorPalette="red"
                onClick={handleStop}
                disabled={routing}
              >
                <HiStop />
                Stop
              </Button>
            ) : null}
          </HStack>
        </VStack>
      </Panel>

      {audioTips?.length ? (
        <Box
          as="details"
          borderRadius="lg"
          border="1px solid"
          borderColor="gray.800"
          bg="gray.900/50"
          px={3}
          py={2}
        >
          <HStack
            as="summary"
            cursor="pointer"
            listStyleType="none"
            userSelect="none"
            py={1}
            gap={2}
            css={{ "&::-webkit-details-marker": { display: "none" } }}
            _hover={{ color: "gray.300" }}
            color="gray.500"
            fontSize="xs"
            fontWeight="medium"
          >
            <LuChevronRight size={14} />
            <Text as="span">Audio quality tips</Text>
          </HStack>
          <Box as="ul" mt={2} pl={6} pb={1} style={{ listStyleType: "disc" }} fontSize="xs" color="gray.500">
            {audioTips.map((t) => (
              <li key={t} style={{ marginBottom: "0.35em" }}>
                {t}
              </li>
            ))}
          </Box>
        </Box>
      ) : null}

      <Separator borderColor="gray.800" />
      <Text fontSize="xs" color="gray.600" textAlign="center">
        TASCAM US-1800 Native Driver for macOS Apple Silicon & Intel
      </Text>
    </VStack>
  );
}
