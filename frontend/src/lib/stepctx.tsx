import { createContext, useContext } from 'react'

/** The current wizard step ("03", "Scenarios"), so every page header can show where the user is. */
export const StepContext = createContext<{ n: string; name: string } | null>(null)
export const useStepInfo = () => useContext(StepContext)
