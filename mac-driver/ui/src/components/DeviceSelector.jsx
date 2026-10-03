import { Box, Text, HStack, Badge } from "@chakra-ui/react";
import { LuUsb } from "react-icons/lu";

export default function DeviceSelector({ device }) {
  const ok = Boolean(device);

  return (
    <Box
      bg="gray.900"
      p={4}
      borderRadius="xl"
      border="1px solid"
      borderColor={ok ? "teal.900" : "gray.800"}
      boxShadow="sm"
    >
      <HStack gap={3} align="flex-start">
        <Box
          color={ok ? "teal.400" : "gray.600"}
          mt={0.5}
          aria-hidden
        >
          <LuUsb size={22} />
        </Box>
        <Box flex={1} minW={0}>
          <HStack gap={2} align="center" mb={0.5}>
            <Text fontWeight="semibold" color="gray.100" fontSize="md">
              {device?.name || "TASCAM US-1800"}
            </Text>
            {ok ? (
              <Badge colorPalette="teal" variant="subtle" size="sm">
                Detected
              </Badge>
            ) : null}
          </HStack>
          <Text fontSize="xs" color="gray.500">
            {device
              ? `Firmware ${device.firmware} · ${device.vid}:${device.pid} · 16 IN / 4 OUT + MIDI`
              : "Connect the interface via USB, then refresh."}
          </Text>
        </Box>
      </HStack>
    </Box>
  );
}
