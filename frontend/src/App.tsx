import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const queryClient = new QueryClient();

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <div className="app-root">
        <h1>Study Notes Parser</h1>
      </div>
    </QueryClientProvider>
  );
}
