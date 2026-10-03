import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ChakraProvider, defaultSystem } from "@chakra-ui/react";
import { ThemeProvider } from "next-themes";
import App from "./App";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <ChakraProvider value={defaultSystem}>
      <ThemeProvider attribute="class" defaultTheme="dark" forcedTheme="dark">
        <App />
      </ThemeProvider>
    </ChakraProvider>
  </StrictMode>
);
