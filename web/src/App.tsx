import { Shell } from "./components/Shell/Shell.tsx";
import { Playground } from "./playground/Playground.tsx";
import { useRoute } from "./router/router.ts";

export function App() {
  const [route] = useRoute();
  if (route.name === "playground") {
    return <Playground />;
  }
  return <Shell />;
}
