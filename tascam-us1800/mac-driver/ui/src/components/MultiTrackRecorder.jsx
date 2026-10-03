import { useEffect, useState } from "react";
import {
  Box,
  HStack,
  VStack,
  Text,
  Button,
  Badge,
  Input,
} from "@chakra-ui/react";
import { LuMic, LuSquare, LuDownload, LuTrash2, LuRefreshCw } from "react-icons/lu";
import {
  startRecording,
  stopRecording,
  getRecordings,
  deleteRecording,
} from "../api";

export default function MultiTrackRecorder({ stats, sampleRate, onAction }) {
  const isRecording = Boolean(stats?.is_recording);
  const [recordings, setRecordings] = useState([]);
  const [loading, setLoading] = useState(false);
  const [name, setName] = useState("");

  const refreshRecordings = () => {
    getRecordings()
      .then((d) => setRecordings(d?.recordings || []))
      .catch(() => {});
  };

  useEffect(() => {
    refreshRecordings();
    const interval = setInterval(refreshRecordings, 4000);
    return () => clearInterval(interval);
  }, []);

  const handleToggleRecord = async () => {
    setLoading(true);
    try {
      if (isRecording) {
        await stopRecording();
      } else {
        await startRecording({ sample_rate: sampleRate, name: name.trim() });
        setName("");
      }
      onAction?.();
      refreshRecordings();
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (filename) => {
    await deleteRecording(filename);
    refreshRecordings();
  };

  return (
    <Box bg="gray.900" p={4} borderRadius="xl" border="1px solid" borderColor="gray.800">
      <HStack justify="space-between" align="center" mb={3}>
        <Text fontSize="xs" color="gray.500" fontWeight="medium" textTransform="uppercase" letterSpacing="0.06em">
          16-Track Audio Recorder
        </Text>
        {isRecording ? (
          <Badge colorPalette="red" variant="solid" size="sm">
            ● RECORDING {stats?.recording_elapsed != null ? `${stats.recording_elapsed}s` : ""}
          </Badge>
        ) : (
          <Badge colorPalette="gray" variant="subtle" size="sm">
            24-BIT · 16 CHANNELS
          </Badge>
        )}
      </HStack>

      <VStack gap={3} align="stretch">
        <HStack gap={2}>
          <Input
            placeholder="Recording tag (optional)"
            size="sm"
            bg="gray.800"
            border="1px solid"
            borderColor="gray.700"
            color="gray.100"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={isRecording}
          />
          <Button
            size="sm"
            colorPalette={isRecording ? "red" : "green"}
            onClick={handleToggleRecord}
            loading={loading}
          >
            {isRecording ? <LuSquare /> : <LuMic />}
            {isRecording ? "Stop Recording" : "Record 16 Tracks"}
          </Button>
        </HStack>

        {isRecording && stats?.recording_file && (
          <Text fontSize="xs" color="gray.400">
            Writing to: <Text as="span" color="teal.300">{stats.recording_file}</Text>
          </Text>
        )}

        {/* Recordings History */}
        <Box pt={2} borderTop="1px solid" borderColor="gray.800">
          <HStack justify="space-between" align="center" mb={2}>
            <Text fontSize="2xs" color="gray.500" fontWeight="semibold">
              SAVED RECORDINGS ({recordings.length})
            </Text>
            <Button size="xs" variant="ghost" color="gray.400" onClick={refreshRecordings}>
              <LuRefreshCw size={12} />
            </Button>
          </HStack>

          {recordings.length === 0 ? (
            <Text fontSize="xs" color="gray.600">
              No recordings yet. Click Record 16 Tracks to capture all inputs.
            </Text>
          ) : (
            <VStack gap={1.5} align="stretch" maxH="140px" overflowY="auto">
              {recordings.slice(0, 5).map((rec) => (
                <HStack
                  key={rec.filename}
                  justify="space-between"
                  p={1.5}
                  bg="gray.850"
                  borderRadius="md"
                  fontSize="xs"
                >
                  <VStack align="flex-start" gap={0} minW={0}>
                    <Text color="gray.200" truncate maxW="180px">
                      {rec.filename}
                    </Text>
                    <Text fontSize="2xs" color="gray.500">
                      {rec.size_mb} MB · ~{rec.duration_sec}s · {rec.date}
                    </Text>
                  </VStack>
                  <HStack gap={1}>
                    <Button
                      as="a"
                      href={`/api/recordings/${encodeURIComponent(rec.filename)}`}
                      download
                      size="xs"
                      colorPalette="cyan"
                      variant="ghost"
                      title="Download WAV"
                    >
                      <LuDownload size={14} />
                    </Button>
                    <Button
                      size="xs"
                      colorPalette="red"
                      variant="ghost"
                      onClick={() => handleDelete(rec.filename)}
                      title="Delete"
                    >
                      <LuTrash2 size={14} />
                    </Button>
                  </HStack>
                </HStack>
              ))}
            </VStack>
          )}
        </Box>
      </VStack>
    </Box>
  );
}
