use std::fmt;

#[derive(Debug)]
pub enum TomoError { Io(std::io::Error), Message(String) }

impl fmt::Display for TomoError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            TomoError::Io(err) => write!(f, "I/O error: {err}"),
            TomoError::Message(msg) => write!(f, "{msg}"),
        }
    }
}
impl From<std::io::Error> for TomoError { fn from(value: std::io::Error) -> Self { TomoError::Io(value) } }
