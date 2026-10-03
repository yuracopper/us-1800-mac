import { Box, HStack, VStack, Text, Badge } from "@chakra-ui/react";

function MeterBar({ label, value = 0, color = "teal" }) {
  const clamped = Math.max(0, Math.min(1, value));
  const pct = Math.round(clamped * 100);
  const isHot = clamped > 0.9;
  const isWarm = clamped > 0.7;
  const barColor = isHot ? "red.400" : isWarm ? "yellow.400" : `${color}.400`;

  return (
    <VStack gap={1} align="center" minW="22px">
      <Box
        w="16px"
        h="70px"
        bg="gray.800"
        borderRadius="sm"
        position="relative"
        overflow="hidden"
        border="1px solid"
        borderColor="gray.700"
      >
        <Box
          position="absolute"
          bottom={0}
          left={0}
          right={0}
          h={`${pct}%`}
          bg={barColor}
          transition="height 0.08s ease-out"
        />
      </Box>
      <Text fontSize="2xs" color="gray.400" fontFamily="mono" tabularNums>
        {label}
      </Text>
    </VStack>
  );
}

export default function VuMeters({ inPeak = [], outPeak = [] }) {
  return (
    <Box bg="gray.900" p={4} borderRadius="xl" border="1px solid" borderColor="gray.800">
      <HStack justify="space-between" align="center" mb={3}>
        <Text fontSize="xs" color="gray.500" fontWeight="medium" textTransform="uppercase" letterSpacing="0.06em">
          Live VU Meters
        </Text>
        <Badge colorPalette="cyan" variant="subtle" size="sm">
          16 IN · 4 OUT
        </Badge>
      </HStack>

      <VStack gap={4} align="stretch">
        {/* 16 Inputs */}
        <Box>
          <Text fontSize="2xs" color="gray.400" mb={2} fontWeight="semibold">
            HARDWARE INPUTS (16 CHANNELS)
          </Text>
          <HStack gap={3} overflowX="auto" pb={1} justify="space-between">
            {/* Mic 1-8 */}
            <Box>
              <Text fontSize="3xs" color="gray.500" mb={1} textAlign="center">
                MIC 1-8 (XLR)
              </Text>
              <HStack gap={1}>
                {[0, 1, 2, 3, 4, 5, 6, 7].map((ch) => (
                  <MeterBar key={ch} label={ch + 1} value={inPeak[ch] || 0} color="green" />
                ))}
              </HStack>
            </Box>

            {/* Line/Inst 9-10 */}
            <Box>
              <Text fontSize="3xs" color="gray.500" mb={1} textAlign="center">
                GTR 9-10
              </Text>
              <HStack gap={1}>
                {[8, 9].map((ch) => (
                  <MeterBar key={ch} label={ch + 1} value={inPeak[ch] || 0} color="blue" />
                ))}
              </HStack>
            </Box>

            {/* Line 11-14 */}
            <Box>
              <Text fontSize="3xs" color="gray.500" mb={1} textAlign="center">
                LINE 11-14
              </Text>
              <HStack gap={1}>
                {[10, 11, 12, 13].map((ch) => (
                  <MeterBar key={ch} label={ch + 1} value={inPeak[ch] || 0} color="cyan" />
                ))}
              </HStack>
            </Box>

            {/* S/PDIF 15-16 */}
            <Box>
              <Text fontSize="3xs" color="gray.500" mb={1} textAlign="center">
                S/PDIF
              </Text>
              <HStack gap={1}>
                {[14, 15].map((ch) => (
                  <MeterBar key={ch} label={ch + 1} value={inPeak[ch] || 0} color="purple" />
                ))}
              </HStack>
            </Box>
          </HStack>
        </Box>

        {/* 4 Outputs */}
        <Box pt={2} borderTop="1px solid" borderColor="gray.800">
          <Text fontSize="2xs" color="gray.400" mb={2} fontWeight="semibold">
            HARDWARE OUTPUTS (4 CHANNELS)
          </Text>
          <HStack gap={2}>
            <HStack gap={1}>
              <MeterBar label="L1" value={outPeak[0] || 0} color="teal" />
              <MeterBar label="R2" value={outPeak[1] || 0} color="teal" />
            </HStack>
            <HStack gap={1} ml={4}>
              <MeterBar label="L3" value={outPeak[2] || 0} color="teal" />
              <MeterBar label="R4" value={outPeak[3] || 0} color="teal" />
            </HStack>
          </HStack>
        </Box>
      </VStack>
    </Box>
  );
}
