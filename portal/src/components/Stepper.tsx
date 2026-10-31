

type StepperProps = {
  steps: string[]
  currentStep: number
  /** When given, every step becomes a button that jumps to that step. */
  onStepClick?: (stepNumber: number) => void
}

export default function Stepper({
  steps,
  currentStep,
  onStepClick,
}: StepperProps) {
  return (
    <ol className="ui-stepper" aria-label="Progress">
      {steps.map((step, index) => {
        const stepNumber = index + 1
        const completed = stepNumber < currentStep
        const current = stepNumber === currentStep

        const content = (
          <>
            <span className="ui-stepper__number">
              {completed ? 'Done' : stepNumber}
            </span>

            <span className="ui-stepper__label">{step}</span>
          </>
        )

        return (
          <li
            key={`${stepNumber}-${step}`}
            className={[
              'ui-stepper__item',
              completed ? 'is-complete' : '',
              current ? 'is-current' : '',
            ]
              .filter(Boolean)
              .join(' ')}
            aria-current={current ? 'step' : undefined}
          >
            {onStepClick ? (
              <button
                type="button"
                className="ui-stepper__button"
                onClick={() => onStepClick(stepNumber)}
                aria-label={`Go to step ${stepNumber}: ${step}`}
              >
                {content}
              </button>
            ) : (
              content
            )}
          </li>
        )
      })}
    </ol>
  )
}
