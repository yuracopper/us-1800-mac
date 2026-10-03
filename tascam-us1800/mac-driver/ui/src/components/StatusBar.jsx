import { HStack, Text, Box, Badge } from "@chakra-ui/react";
import { LuCircle, LuClock } from "react-icons/lu";

function formatElapsed(sec) {
  if (sec == null) return "—";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export default function StatusBar({ stats, connected }) {
  const isStreaming = stats?.streaming;
  const lab = stats?.passthrough_lab;

  const modeLabel =
    stats?.mode === "tone" ? "Test tone" : stats?.mode === "passthrough" ? "Passthrough" : null;

  const modeTitle =
    lab &&
    [
      lab.capture_backend,
      lab.capture_sample_rate != null ? `${lab.capture_sample_rate} Hz` : null,
      lab.capture_sr_forced ? "forced SR" : null,
      lab.macos_output_volume != null ? `macOS vol ${lab.macos_output_volume}%` : null,
      lab.resampler && lab.resampler !== "none" ? lab.resampler : null,
    ]
      .filter(Boolean)
      .join(" · ");

  const usbBrief =
    stats?.sample_rate != null && stats?.channels != null
      ? `${(stats.sample_rate / 1000).toFixed(1)} kHz · ${stats.channels}ch`
      : null;

  return (
    <Box
      px={{ base: 3, md: 4 }}
      py={2.5}
      bg="gray.900"
      borderBottom="1px solid"
      borderColor="gray.700"
      flexShrink={0}
    >
      <HStack justify="space-between" align="flex-start" gap={3} flexWrap="wrap">
        <HStack gap={2} minW={0} align="center">
          <Text fontWeight="semibold" fontSize="md" color="gray.100" letterSpacing="tight">
            US-1800
          </Text>
          <Badge colorPalette={connected ? "green" : "red"} variant="subtle" size="sm">
            <HStack gap={1}>
              <LuCircle size={8} fill="currentColor" />
              <Text>{connected ? "Ready" : "Disconnected"}</Text>
            </HStack>
          </Badge>
          {stats?.is_recording && (
            <Badge colorPalette="red" variant="solid" size="sm">
              REC {stats.recording_elapsed ? `${stats.recording_elapsed}s` : ""}
            </Badge>
          )}
          {connected && (
            <Badge colorPalette="purple" variant="outline" size="sm">
              MIDI Ready
            </Badge>
          )}
        </HStack>

        {isStreaming && (
          <HStack gap={2} flexWrap="wrap" justify="flex-end" align="center">
            {modeLabel ? (
              <Badge
                colorPalette="teal"
                variant="outline"
                size="sm"
                title={modeTitle || undefined}
                cursor={modeTitle ? "help" : undefined}
              >
                {modeLabel}
              </Badge>
            ) : null}
            {usbBrief ? (
              <Text fontSize="sm" color="gray.400" fontWeight="medium">
                {usbBrief}
              </Text>
            ) : null}
            <HStack gap={1} fontSize="xs" color="gray.500" title="Nominal PCM bytes/s">
              <Text>~{(stats.output_rate / 1000).toFixed(0)} kB/s</Text>
            </HStack>
            <HStack gap={1} fontSize="sm" color="gray.400">
              <LuClock size={14} />
              <Text tabularNums>{formatElapsed(stats.elapsed)}</Text>
            </HStack>
          </HStack>
        )}
      </HStack>
    </Box>
  );
}
