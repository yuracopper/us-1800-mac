import { useEffect, useRef, useState } from "react";
import { Box, Container } from "@chakra-ui/react";
import StatusBar from "./components/StatusBar";
import DeviceSelector from "./components/DeviceSelector";
import StreamControls from "./components/StreamControls";
import { getStatus, connectWS } from "./api";

export default function App() {
  const [stats, setStats] = useState(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  useEffect(() => {
    getStatus()
      .then((s) => {
        setStats(s);
        setConnected(s.device_connected);
      })
      .catch(() => setConnected(false));

    const conn = connectWS((data) => {
      setStats(data);
      setConnected(data.device_connected);
    });
    wsRef.current = conn;

    return () => {
      conn.close();
      wsRef.current = null;
    };
  }, []);

  const refresh = () => {
    getStatus().then(setStats).catch(() => {});
  };

  return (
    <Box minH="100vh" bg="gray.950" color="gray.100">
      <StatusBar stats={stats} connected={connected} />
      <Container maxW="lg" py={6} px={{ base: 4, md: 6 }} display="flex" flexDirection="column" gap={5}>
        <DeviceSelector
          device={
            connected
              ? {
                  name: "TASCAM US-1800",
                  firmware: stats?.firmware || "1.49",
                  vid: "0644",
                  pid: "8030",
                }
              : null
          }
        />
        <StreamControls
          stats={stats}
          onAction={refresh}
          wsSendVolume={(v) => wsRef.current?.sendVolume?.(v)}
        />
      </Container>
    </Box>
  );
}
