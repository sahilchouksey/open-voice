import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  InputHTMLAttributes,
  LabelHTMLAttributes,
  SelectHTMLAttributes,
} from "react"

export function Card(props: HTMLAttributes<HTMLElement>) {
  const { className = "", ...rest } = props
  return <section className={`window ui-card ${className}`.trim()} {...rest} />
}

export function Label(props: LabelHTMLAttributes<HTMLLabelElement>) {
  const { className = "", ...rest } = props
  return <label className={`ui-label ${className}`.trim()} {...rest} />
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  const { className = "", ...rest } = props
  return <input className={`ui-input ${className}`.trim()} {...rest} />
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  const { className = "", ...rest } = props
  return <select className={`ui-select ${className}`.trim()} {...rest} />
}

export function Button(props: ButtonHTMLAttributes<HTMLButtonElement>) {
  const { className = "", type, ...rest } = props
  return <button className={`ui-button ${className}`.trim()} type={type ?? "button"} {...rest} />
}

export function TabsList(props: HTMLAttributes<HTMLElement>) {
  const { className = "", ...rest } = props
  return <nav className={`window mode-tabs ui-tabs-list ${className}`.trim()} {...rest} />
}

export function TabsTrigger(
  props: ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean },
) {
  const { className = "", active, ...rest } = props
  return (
    <button
      className={`tab-btn ui-tabs-trigger${active ? " active" : ""} ${className}`.trim()}
      type="button"
      {...rest}
    />
  )
}
